#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

static uintptr_t image_base;
static std::atomic<bool> pending(false);
static constexpr std::array<uintptr_t, 8> tables = {
    0xfda6d58, 0xff2b878, 0xff2bbc8, 0xff2bf18, 0xff2b528,
    0xffb5c38, 0xffeb5a0, 0xffeb6b8
};
using Snapshot = std::array<std::vector<uintptr_t>, tables.size()>;

template <typename Function> static Function native(uintptr_t offset) {
    return reinterpret_cast<Function>(image_base + offset);
}

static Snapshot find_objects() {
    Snapshot objects;
    std::ifstream maps("/proc/self/maps");
    std::string line;
    int descriptor = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return objects;
    std::vector<uintptr_t> buffer(32768);
    while (std::getline(maps, line)) {
        unsigned long start, end, offset;
        char permissions[8] = {};
        if (std::sscanf(line.c_str(), "%lx-%lx %7s %lx", &start, &end, permissions, &offset) != 4) continue;
        if (std::strcmp(permissions, "rw-p") || line.find('/') != std::string::npos || line.find("[stack]") != std::string::npos) continue;
        for (uintptr_t position = start; position < end; position += buffer.size() * sizeof(uintptr_t)) {
            size_t size = std::min(end - position, buffer.size() * sizeof(uintptr_t));
            ssize_t received = pread(descriptor, buffer.data(), size, position);
            if (received <= 0) continue;
            for (size_t index = 0; index < static_cast<size_t>(received) / sizeof(uintptr_t); ++index) {
                uintptr_t address = position + index * sizeof(uintptr_t);
                uintptr_t buffer_start = reinterpret_cast<uintptr_t>(buffer.data());
                if (address >= buffer_start && address < buffer_start + buffer.size() * sizeof(uintptr_t)) continue;
                uintptr_t candidate = buffer[index];
                if (candidate < image_base + tables.front() || candidate > image_base + tables.back() + 16) continue;
                for (size_t target = 0; target < tables.size(); ++target) {
                    if (candidate == image_base + tables[target] + 16) objects[target].push_back(address);
                }
            }
        }
    }
    close(descriptor);
    return objects;
}

static void *object(const Snapshot &snapshot, size_t target) {
    if (snapshot[target].size() != 1) return nullptr;
    uintptr_t address = snapshot[target].front(), value = 0;
    int descriptor = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return nullptr;
    ssize_t received = pread(descriptor, &value, sizeof(value), address);
    close(descriptor);
    return received == static_cast<ssize_t>(sizeof(value)) && value == image_base + tables[target] + 16 ? reinterpret_cast<void *>(address) : nullptr;
}

static void apply_media(const Snapshot &snapshot, const std::string &title, const std::string &artist, const std::string &source) {
    using TextSetter = void (*)(void *, const std::string &);
    constexpr std::array<uintptr_t, 4> title_updaters = {0x7435b24, 0x74387e0, 0x743a90c, 0x7430d40};
    for (size_t target = 1; target <= 4; ++target) {
        void *adapter = object(snapshot, target);
        if (!adapter || !*reinterpret_cast<void **>(reinterpret_cast<uintptr_t>(adapter) + 0x20)) continue;
        native<void (*)(void *, int)>(0xbdebb8c)(adapter, 0);
        native<TextSetter>(0xbdebf00)(adapter, title);
        native<TextSetter>(0xbdebfa0)(adapter, artist);
        native<void (*)(void *, std::string)>(title_updaters[target - 1])(adapter, source);
    }
}

struct Temperature {
    unsigned zone;
    float value;
    unsigned char quality;
};

struct SystemOn {
    unsigned zone;
    unsigned char on;
    unsigned char quality;
};

struct alignas(8) Message32 {
    std::array<unsigned char, 32> bytes;
};

static void apply_climate(const Snapshot &snapshot, float left, float right, bool system_on) {
    void *climate = object(snapshot, 5);
    using Notification = void (*)(void *, const std::vector<Message32> &);
    if (climate) {
        std::vector<Message32> temperatures, on_states;
        for (unsigned zone : {0u, 1u}) {
            Temperature temperature{zone, zone ? right : left, 0};
            SystemOn on_state{zone, static_cast<unsigned char>(system_on), 0};
            temperatures.push_back(native<Message32 (*)(const Temperature &)>(0x7c31ee0)(temperature));
            on_states.push_back(native<Message32 (*)(const SystemOn &)>(0x7c32140)(on_state));
        }
        native<Notification>(0x7bcee2c)(climate, on_states);
        native<Notification>(0x7bcc370)(climate, temperatures);
    }
    using BoolSetter = void (*)(void *, const bool &, unsigned, bool, bool);
    using TextSetter = void (*)(void *, const std::string &, unsigned, bool, bool);
    for (size_t target : {6u, 7u}) {
        void *bar = object(snapshot, target);
        if (!bar) continue;
        void *slider = *reinterpret_cast<void **>(reinterpret_cast<uintptr_t>(bar) + 0x20);
        if (!slider) continue;
        float temperature = target == 6 ? left : right;
        native<void (*)(void *, float, int)>(0x7d5dd04)(bar, temperature, 0);
        bool off = !system_on;
        native<TextSetter>(0xcd1f9e0)(slider, std::string("A/C OFF"), 0, true, true);
        native<BoolSetter>(0xcd28d6c)(slider, off, 0, true, true);
        if (off) native<TextSetter>(0xcd1fae8)(slider, std::string("OFF"), 0, true, true);
        native<TextSetter>(0xcd1ffa4)(slider, std::string(system_on ? "°C" : ""), 0, true, true);
        native<TextSetter>(0xcd1fb6c)(slider, std::string("AUTO"), 0, true, true);
        native<BoolSetter>(0xcd293c0)(slider, system_on, 0, true, true);
    }
}

static void apply_state(const Snapshot &snapshot, const std::string &document) {
    std::istringstream input(document);
    std::string title, artist, source;
    std::getline(input, title);
    std::getline(input, artist);
    std::getline(input, source);
    float left = 21.0f, right = 22.0f;
    int system_on = 1;
    input >> left >> right >> system_on;
    if (!std::isfinite(left) || !std::isfinite(right) || left < 16 || left > 28 || right < 16 || right > 28) {
        pending = false;
        return;
    }
    apply_media(snapshot, title, artist, source);
    apply_climate(snapshot, left, right, system_on != 0);
    static std::string previous;
    if (document != previous) {
        std::fprintf(stderr, "lab-mock: native mock state updated (media / climate), app tid=%ld\n", syscall(SYS_gettid));
        previous = document;
    }
    pending = false;
}

// On a real car CLanguageService::setDisplayLanguage sends the choice to the vehicle units service and waits for
// that service to report it back. The emulator has no such service (the proxy pointer stays null and the call
// returns false immediately), so picking a language in the menu did nothing. This replaces the function with one
// that reports the chosen language back to the service directly, exactly like the vehicle's answer would.
namespace language {
constexpr uintptr_t set_display_language = 0x7b67a40, process_incoming = 0x7b68564, language_table = 0x107fdaa0;
constexpr uint32_t paciasp = 0xd503233f;

struct Entry {  // 24 bytes: ELanguage, then the VehicleTypes::Language object (vtable, value)
    uint16_t language;
    uint8_t padding[6];
    uintptr_t vtable;
    uint8_t value;
    uint8_t rest[7];
};

static bool set(void *service, uint16_t requested) {
    auto *table = reinterpret_cast<Entry *const *>(image_base + language_table);
    const Entry *begin = table[0], *end = table[1];
    if (!service || !begin || end < begin) return false;
    for (const Entry *entry = begin; entry != end; ++entry) {
        if (entry->language != requested) continue;
        std::fprintf(stderr, "lab-mock: display language %u -> vehicle language %u\n", requested, entry->value);
        // processIncomingLanguage takes the Language object (vtable at +0, value at +8), i.e. entry + 8.
        native<void (*)(void *, const void *, bool)>(process_incoming)(service, reinterpret_cast<const uint8_t *>(entry) + 8, true);
        return true;
    }
    std::fprintf(stderr, "lab-mock: display language %u is not in the language table\n", requested);
    return false;
}

static void install() {
    uintptr_t target = image_base + set_display_language;
    if (*reinterpret_cast<uint32_t *>(target) != paciasp) {
        std::fprintf(stderr, "lab-mock: language hook signature mismatch; disabled\n");
        return;
    }
    // ldr x16, #8 ; br x16 ; .quad hook
    uint32_t patch[4] = {0x58000050, 0xd61f0200, 0, 0};
    uintptr_t hook = reinterpret_cast<uintptr_t>(&set);
    std::memcpy(&patch[2], &hook, sizeof(hook));
    int descriptor = open("/proc/self/mem", O_RDWR | O_CLOEXEC);
    bool written = descriptor >= 0 && pwrite(descriptor, patch, sizeof(patch), static_cast<off_t>(target)) == static_cast<ssize_t>(sizeof(patch));
    if (descriptor >= 0) close(descriptor);
    if (!written) {
        std::fprintf(stderr, "lab-mock: cannot patch setDisplayLanguage; language selection stays disabled\n");
        return;
    }
    __builtin___clear_cache(reinterpret_cast<char *>(target), reinterpret_cast<char *>(target + sizeof(patch)));
    std::fprintf(stderr, "lab-mock: display language selection enabled\n");
}
}  // namespace language

static void *worker(void *) {
    std::this_thread::sleep_for(std::chrono::seconds(60));
    auto dispatch = native<void (*)(void *, std::function<void ()>, std::shared_ptr<void>)>(0x510c568);
    Snapshot snapshot;
    unsigned ticks = 0;
    for (;;) {
        std::ifstream state("/run/lab-mock/state.txt");
        std::ostringstream contents;
        contents << state.rdbuf();
        std::string document = contents.str();
        if (!document.empty() && !pending.exchange(true)) {
            if (ticks++ % 10 == 0 || !object(snapshot, 0)) snapshot = find_objects();
            if (void *application = object(snapshot, 0)) {
                dispatch(application, [snapshot, document] { apply_state(snapshot, document); }, {});
            } else {
                pending = false;
            }
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    return nullptr;
}

__attribute__((constructor)) static void initialize() {
    Dl_info information = {};
    void *symbol = dlsym(RTLD_DEFAULT, "_ZN12CHmiProcUnit12dispatchCallESt8functionIFvvEESt10shared_ptrI29CHmiDispatchedCallEventAnchorE");
    if (!symbol || !dladdr(symbol, &information)) {
        std::fprintf(stderr, "lab-mock: native dispatcher unavailable; disabled\n");
        return;
    }
    image_base = reinterpret_cast<uintptr_t>(information.dli_fbase);
    if (symbol != reinterpret_cast<void *>(image_base + 0x510c568)) {
        std::fprintf(stderr, "lab-mock: dispatcher signature mismatch; disabled\n");
        return;
    }
    language::install();
    pthread_t thread;
    if (!pthread_create(&thread, nullptr, worker, nullptr)) pthread_detach(thread);
    std::fprintf(stderr, "lab-mock: experimental native state mock enabled (not Bluetooth)\n");
}
