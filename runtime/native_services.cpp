namespace service_mock {
static constexpr std::array<uintptr_t, 7> targets = {
    0xffad018, 0xffae398, 0x1028a9b8, 0xffbd6b8, 0x1016b850,
    0xffb5c38, 0xffaeec0
};
using Objects = std::array<std::vector<uintptr_t>, targets.size()>;
static std::atomic<bool> queued(false);
static uint64_t started_ns;

static uint64_t realtime_ns() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}

static bool write_file(const char *destination, const std::string &text, bool durable = false) {
    std::string pattern = std::string(destination) + ".XXXXXX";
    std::vector<char> path(pattern.begin(), pattern.end());
    path.push_back(0);
    int descriptor = mkstemp(path.data());
    if (descriptor < 0) return false;
    bool saved = write(descriptor, text.data(), text.size()) == static_cast<ssize_t>(text.size());
    if (durable && fsync(descriptor) != 0) saved = false;
    if (close(descriptor) != 0) saved = false;
    if (saved) saved = std::rename(path.data(), destination) == 0;
    if (!saved) unlink(path.data());
    return saved;
}

static Objects discover() {
    Objects result;
    std::ifstream maps("/proc/self/maps");
    std::string line;
    int descriptor = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return result;
    std::vector<uintptr_t> buffer(32768);
    while (std::getline(maps, line)) {
        unsigned long start, end, offset;
        char permissions[8] = {};
        if (std::sscanf(line.c_str(), "%lx-%lx %7s %lx", &start, &end, permissions, &offset) != 4
            || std::strcmp(permissions, "rw-p") || line.find('/') != std::string::npos
            || line.find("[stack]") != std::string::npos) continue;
        for (uintptr_t position = start; position < end; position += buffer.size() * sizeof(uintptr_t)) {
            ssize_t received = pread(descriptor, buffer.data(), std::min(end - position, buffer.size() * sizeof(uintptr_t)), position);
            if (received <= 0) continue;
            for (size_t index = 0; index < static_cast<size_t>(received) / sizeof(uintptr_t); ++index) {
                uintptr_t address = position + index * sizeof(uintptr_t);
                uintptr_t own = reinterpret_cast<uintptr_t>(buffer.data());
                if (address >= own && address < own + buffer.size() * sizeof(uintptr_t)) continue;
                for (size_t target = 0; target < targets.size(); ++target)
                    if (buffer[index] == image_base + targets[target] + 16) result[target].push_back(address);
            }
        }
    }
    close(descriptor);
    return result;
}

static void *checked(const Objects &objects, size_t target) {
    if (objects[target].size() != 1) return nullptr;
    int descriptor = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return nullptr;
    uintptr_t value = 0, address = objects[target].front();
    ssize_t received = pread(descriptor, &value, sizeof(value), address);
    close(descriptor);
    return received == static_cast<ssize_t>(sizeof(value)) && value == image_base + targets[target] + 16
        ? reinterpret_cast<void *>(address) : nullptr;
}

static void temperature(void *bar, float delta) {
    if (!bar) return;
    uintptr_t table = *static_cast<uintptr_t *>(bar);
    int zone = table == image_base + 0xffeb5b0 ? 0 : table == image_base + 0xffeb6c8 ? 1 : -1;
    if (zone < 0) return;
    float left = 21, right = 22;
    int on = 1;
    std::ifstream saved("/run/lab-mock/climate-feedback.txt");
    if (!(saved >> left >> right >> on)) {
        std::ifstream original("/run/lab-mock/state.txt");
        std::string ignored;
        for (unsigned line = 0; line < 3; ++line) std::getline(original, ignored);
        if (!(original >> left >> right >> on)) return;
    }
    if (!std::isfinite(left) || !std::isfinite(right) || left < 16 || left > 28 || right < 16 || right > 28
        || (on != 0 && on != 1)) return;
    float &value = zone ? right : left;
    value = std::clamp(value + delta, 16.0f, 28.0f);
    std::ostringstream text;
    text << left << ' ' << right << ' ' << on << '\n';
    if (!write_file("/run/lab-mock/climate-feedback.txt", text.str(), true)) return;
    native<void (*)(void *, float, int)>(0x7d5dd04)(bar, value, 0);
    std::fprintf(stderr, "lab-services: native climate touch zone=%d value=%.1f\n", zone, value);
}

static void increase(void *bar, unsigned, unsigned long) { temperature(bar, .5f); }
static void decrease(void *bar, unsigned, unsigned long) { temperature(bar, -.5f); }

static bool install_temperature() {
    const std::array<uintptr_t, 2> offsets = {0x7d62acc, 0x7d62124};
    const std::array<uintptr_t, 2> hooks = {reinterpret_cast<uintptr_t>(&increase), reinterpret_cast<uintptr_t>(&decrease)};
    for (uintptr_t offset : offsets)
        if (*reinterpret_cast<uint32_t *>(image_base + offset) != 0xd503233f) return false;
    int descriptor = open("/proc/self/mem", O_RDWR | O_CLOEXEC);
    if (descriptor < 0) return false;
    bool success = true;
    for (size_t index = 0; index < offsets.size(); ++index) {
        uint32_t code[4] = {0x58000050, 0xd61f0200, 0, 0};
        std::memcpy(&code[2], &hooks[index], sizeof(uintptr_t));
        uintptr_t address = image_base + offsets[index];
        if (pwrite(descriptor, code, sizeof(code), static_cast<off_t>(address)) != static_cast<ssize_t>(sizeof(code))) success = false;
        __builtin___clear_cache(reinterpret_cast<char *>(address), reinterpret_cast<char *>(address + sizeof(code)));
    }
    close(descriptor);
    return success;
}

struct alignas(8) AvailableMessage { std::array<unsigned char, 40> bytes{}; };
struct EnumRow { unsigned key; unsigned padding; std::array<unsigned char, 16> value; };
static_assert(sizeof(EnumRow) == 24);

static const std::vector<EnumRow> &enum_rows(uintptr_t slot) {
    return **reinterpret_cast<const std::vector<EnumRow> **>(image_base + slot);
}

static void climate_availability(void *climate) {
    if (!climate) return;
    const auto &zones = enum_rows(0x10649c18);
    if (zones.size() > 32) return;
    const std::array<std::pair<uintptr_t, uintptr_t>, 3> features = {{
        {0x106778a8, 0x7bc86c0}, // air blow
        {0x1064df58, 0x7bc91c0}, // temperature
        {0x10674f80, 0x7bc9480}  // air conditioning
    }};
    for (const auto &feature : features) {
        const auto &rows = enum_rows(feature.first);
        if (rows.size() > 64) continue;
        std::vector<AvailableMessage> messages;
        for (const auto &zone : zones) {
            if (zone.key != 1 && zone.key != 2) continue;
            for (const auto &row : rows) {
                AvailableMessage message;
                message.bytes[0] = 8; // Native mapper's available bit.
                std::memcpy(message.bytes.data() + 8, zone.value.data(), 16);
                std::memcpy(message.bytes.data() + 24, row.value.data(), 16);
                messages.push_back(message);
            }
        }
        if (!messages.empty()) native<void (*)(void *, const std::vector<AvailableMessage> &)>(feature.second)(climate, messages);
    }
    struct alignas(8) FeaturesMessage { std::array<unsigned char, 24> bytes{}; };
    std::vector<FeaturesMessage> capabilities;
    for (const auto &zone : zones) {
        if (zone.key != 1 && zone.key != 2) continue;
        FeaturesMessage message;
        std::memcpy(message.bytes.data(), zone.value.data(), 16);
        message.bytes[16] = 0x1f;
        capabilities.push_back(message);
    }
    if (!capabilities.empty()) native<void (*)(void *, const std::vector<FeaturesMessage> &)>(0x7bc9320)(climate, capabilities);
}

static void apply(const Objects &objects, const std::string &document) {
    unsigned version, ignition;
    uint64_t revision, request;
    float speed;
    char gear;
    std::array<unsigned, 6> doors;
    std::string menu;
    std::istringstream input(document);
    if (!(input >> version >> revision >> ignition >> speed >> gear) || version != 1
        || !std::isfinite(speed) || speed < 0 || speed > 330
        || (ignition != 2 && ignition != 5 && ignition != 8 && ignition != 10)) return;
    for (unsigned &value : doors) if (!(input >> value) || value > 1) return;
    if (!(input >> request >> menu) || (menu != "none" && menu != "parking" && menu != "climate")) return;
    std::string indicator = "off", lights = "off";
    if (input >> indicator >> lights) {
        if (indicator != "off" && indicator != "left" && indicator != "right" && indicator != "hazard") return;
        if (lights != "off" && lights != "auto" && lights != "parking" && lights != "low_beam" && lights != "high_beam") return;
    }
    unsigned auto_on = 1, ac_on = 1, fan = 3;
    if (input >> auto_on >> ac_on >> fan) {
        if (auto_on > 1 || ac_on > 1 || fan > 7) return;
    }
    unsigned raw_gear = gear == 'P' ? 1 : gear == 'R' ? 2 : gear == 'N' ? 3 : gear == 'D' ? 4 : 15;
    if (raw_gear == 15) return;
    void *vehicle = checked(objects, 0), *pwf = checked(objects, 1), *staging = checked(objects, 2);
    void *climate = checked(objects, 5), *power = checked(objects, 6);
    static std::string previous_payload;
    static uint64_t last_full = 0;
    const uint64_t now = realtime_ns();
    const bool full = document != previous_payload || now - last_full >= 10000000000ULL;
    if (full) { previous_payload = document; last_full = now; }
    static void *available_climate = nullptr;
    if (climate && available_climate != climate) {
        climate_availability(climate);
        available_climate = climate;
    }
    if (climate && full) {
        struct AutoRequest { unsigned zone; unsigned mode; unsigned char on; unsigned char quality; };
        struct alignas(8) AutoMessage { std::array<unsigned char, 48> bytes; };
        std::vector<AutoMessage> automatic;
        std::vector<Message32> ac, blower;
        for (unsigned zone : {1u, 2u}) {
            // Mode 0 is the request generated by the native AUTO button.
            AutoRequest request_auto{zone, 0, static_cast<unsigned char>(auto_on), 0};
            automatic.push_back(native<AutoMessage (*)(const AutoRequest &)>(0x7c31d6c)(request_auto));
            SystemOn request_ac{zone, static_cast<unsigned char>(ac_on), 0};
            ac.push_back(native<Message32 (*)(const SystemOn &)>(0x7c32260)(request_ac));
            Temperature temporary{zone, 21, 0};
            Message32 status_fan = native<Message32 (*)(const Temperature &)>(0x7c31ee0)(temporary);
            const uint32_t network_level = __builtin_bswap32(fan);
            std::memcpy(status_fan.bytes.data() + 24, &network_level, 4);
            blower.push_back(status_fan);
        }
        native<void (*)(void *, const std::vector<AutoMessage> &)>(0x7bc77c8)(climate, automatic);
        native<void (*)(void *, const std::vector<Message32> &)>(0x7bcdde4)(climate, ac);
        native<void (*)(void *, const std::vector<Message32> &)>(0x7bc8ae0)(climate, blower);
    }
    alignas(8) std::array<unsigned char, 56> condition = {};
    condition[0x18] = static_cast<unsigned char>(ignition);
    if (power && full) native<void (*)(void *, const void *)>(0x7b765cc)(power, condition.data());
    if (pwf && full) native<void (*)(void *, const void *)>(0x7b7080c)(pwf, condition.data());
    if (vehicle && full) {
        uint16_t raw_speed = static_cast<uint16_t>(std::lround(speed * 64));
        native<void (*)(void *, const uint16_t &)>(0x7b4b900)(vehicle, raw_speed);
        alignas(8) std::array<unsigned char, 16> raw = {};
        raw[8] = static_cast<unsigned char>(raw_gear);
        native<void (*)(void *, const void *)>(0x7b4cca0)(vehicle, raw.data());
    }
    if (staging) {
        alignas(8) std::array<unsigned char, 176> panel = {};
        const std::array<size_t, 6> fields = {0xa0, 0x90, 0x80, 0x70, 0x50, 0x60};
        for (size_t index = 0; index < fields.size(); ++index) panel[fields[index]] = static_cast<unsigned char>(doors[index]);
        if (full) native<void (*)(void *, const void *)>(0x8aaf180)(staging, panel.data());
        // Original indicator wrapper stores its enum value at +0x50.
        // This drives the native Live Vehicle model, not an instrument cluster.
        alignas(8) std::array<unsigned char, 88> signals = {};
        const bool phase = (realtime_ns() / 500000000ULL) % 2 == 0;
        signals[0x50] = !phase ? 0 : indicator == "hazard" ? 8 : indicator == "left" ? 1 : indicator == "right" ? 2 : 0;
        native<void (*)(void *, const void *)>(0x8aaf700)(staging, signals.data());
        void *model = static_cast<unsigned char *>(staging) + 0x20;
        const bool low = lights == "low_beam" || lights == "high_beam";
        const bool high = lights == "high_beam";
        const bool running = lights == "auto" || lights == "parking";
        const bool reverse = gear == 'R';
        using SetLight = void (*)(void *, const bool &, bool, bool);
        native<SetLight>(0xc977530)(model, low, true, true);
        native<SetLight>(0xc977a80)(model, high, true, true);
        native<SetLight>(0xc977b90)(model, running, true, true);
        native<SetLight>(0xc976cb0)(model, running, true, true);
        native<SetLight>(0xc977310)(model, reverse, true, true);
    }
    static uint64_t previous_request = 0;
    static std::string result = "none";
    if (request >= started_ns && request != previous_request) {
        previous_request = request;
        if (menu == "parking") {
            if (checked(objects, 4)) {
                native<void (*)(long, bool, unsigned long)>(0x84f66a0)(0x20700000001L, false, 0x20700000000UL);
                result = "parking_menu_requested_no_sensor_backend";
            } else result = "parking_not_coded_or_not_instantiated";
        } else if (menu == "climate") {
            if (void *controller = checked(objects, 3)) {
                native<void (*)(void *, unsigned)>(0x7cb5720)(static_cast<unsigned char *>(controller) + 16, 0);
                result = "climate_menu_requested";
            } else result = "climate_controller_unavailable";
        }
    }
    std::ostringstream report;
    report << "{\"status\":\"native_callbacks_returned\",\"revision\":" << revision
           << ",\"updated_ns\":" << realtime_ns()
           << ",\"vehicle_present\":" << (vehicle ? "true" : "false")
           << ",\"doors_present\":" << (staging ? "true" : "false")
           << ",\"pwf_present\":" << (pwf ? "true" : "false")
           << ",\"parking_present\":" << (checked(objects, 4) ? "true" : "false")
           << ",\"speed_kmh\":" << (vehicle ? native<float (*)(void *)>(0x7b4b8ac)(vehicle) : -1)
           << ",\"gear_code\":" << (vehicle ? native<unsigned (*)(void *)>(0x7b4b828)(vehicle) : 14)
           << ",\"pwf_state\":" << (pwf ? native<unsigned (*)(void *)>(0x7b6ff80)(pwf) : 0)
           << ",\"condition_code\":" << ignition
           << ",\"door_values\":[";
    for (size_t index = 0; index < doors.size(); ++index) {
        if (index) report << ',';
        report << (staging ? static_cast<unsigned>(static_cast<unsigned char *>(staging)[0x88 + index]) : 255);
    }
    report << ']' << ",\"indicator_left\":" << (staging && static_cast<unsigned char *>(staging)[0xa9] ? "true" : "false")
           << ",\"indicator_right\":" << (staging && static_cast<unsigned char *>(staging)[0xa8] ? "true" : "false")
           << ",\"model_low_beam\":" << (staging && native<bool (*)()>(0xc97470c)() ? "true" : "false")
           << ",\"model_high_beam\":" << (staging && native<bool (*)()>(0xc9746a0)() ? "true" : "false")
           << ",\"model_indicator_left\":" << (staging && native<bool (*)()>(0xc974400)() ? "true" : "false")
           << ",\"model_indicator_right\":" << (staging && native<bool (*)()>(0xc97438c)() ? "true" : "false")
           << ",\"light_model_command\":\"" << lights << "\",\"climate_temperatures\":[";
    if (climate) {
        const auto &temperatures = native<const std::vector<Temperature> &(*)(void *)>(0x7bc4870)(climate);
        for (size_t index = 0; index < std::min<size_t>(temperatures.size(), 8); ++index) {
            if (index) report << ',';
            report << "{\"zone\":" << temperatures[index].zone << ",\"value\":";
            if (std::isfinite(temperatures[index].value)) report << temperatures[index].value;
            else report << "null";
            report << '}';
        }
    }
    struct Availability { unsigned zone; unsigned feature; unsigned char available; };
    size_t air_available = climate ? native<const std::vector<Availability> &(*)(void *)>(0x7bc49a0)(climate).size() : 0;
    struct AutoState { unsigned zone; unsigned mode; unsigned char on; unsigned char quality; };
    struct FanState { unsigned level; unsigned zone; unsigned char quality; };
    report << ']' << ",\"climate_auto\":[";
    if (climate) {
        const auto &values = native<const std::vector<AutoState> &(*)(void *)>(0x7bc4c08)(climate);
        for (size_t index = 0; index < std::min<size_t>(values.size(), 8); ++index) {
            if (index) report << ',';
            report << "{\"zone\":" << values[index].zone << ",\"on\":" << unsigned(values[index].on) << '}';
        }
    }
    report << ']' << ",\"climate_fan\":[";
    if (climate) {
        const auto &values = native<const std::vector<FanState> &(*)(void *)>(0x7bc48a0)(climate);
        for (size_t index = 0; index < std::min<size_t>(values.size(), 8); ++index) {
            if (index) report << ',';
            report << "{\"zone\":" << values[index].zone << ",\"level\":" << values[index].level << '}';
        }
    }
    report << ']' << ",\"air_blow_availability_entries\":" << air_available
           << ",\"power_condition_code\":" << (power ? static_cast<unsigned>(static_cast<unsigned char *>(power)[0xad]) : 15)
           << ",\"menu_result\":\"" << result << "\",\"menu_request\":" << request << "}\n";
    write_file("/run/lab-mock/native-feedback.json", report.str());
}

static void *worker(void *) {
    std::this_thread::sleep_for(std::chrono::seconds(15));
    Objects objects;
    Snapshot snapshot;
    std::string previous;
    unsigned ticks = 0;
    uint64_t previous_phase = 0;
    for (;;) {
        std::ifstream input("/run/lab-mock/native-services.txt");
        std::ostringstream text;
        text << input.rdbuf();
        std::string document = text.str();
        bool refresh = ticks++ % 200 == 0;
        uint64_t phase = realtime_ns() / 500000000ULL;
        bool flashing = document.find("\nleft ") != std::string::npos || document.find("\nright ") != std::string::npos
                     || document.find("\nhazard ") != std::string::npos;
        if (!document.empty() && (document != previous || refresh || (flashing && phase != previous_phase)) && !queued.exchange(true)) {
            if (refresh || !checked(objects, 0)) objects = discover();
            if (!object(snapshot, 0)) snapshot = find_objects();
            if (void *application = object(snapshot, 0)) {
                auto dispatch = native<void (*)(void *, std::function<void ()>, std::shared_ptr<void>)>(0x510c568);
                dispatch(application, [objects, document] {
                    try { apply(objects, document); }
                    catch (...) {
                        std::fprintf(stderr, "lab-services: native callback exception\n");
                        write_file("/run/lab-mock/native-feedback.json", "{\"status\":\"native_callback_exception\"}\n");
                    }
                    queued = false;
                }, {});
                previous = document;
                previous_phase = phase;
            } else queued = false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    return nullptr;
}

__attribute__((constructor)) static void install() {
    if (!image_base || *reinterpret_cast<uint32_t *>(image_base + 0x7b4b8b0) != 0xbd41a800) return;
    started_ns = realtime_ns();
    std::fprintf(stderr, "lab-services: native temperature buttons %s\n", install_temperature() ? "enabled" : "unavailable");
    pthread_t thread;
    if (!pthread_create(&thread, nullptr, worker, nullptr)) pthread_detach(thread);
    std::fprintf(stderr, "lab-services: build-gated native service callbacks enabled\n");
}
}
