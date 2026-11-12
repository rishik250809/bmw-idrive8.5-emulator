// Fake parking providers for the MGU22 emulator (definitions: someip-interfaces.json).
//   0x3059/1  ParkmenuAndLayout       requestParkmenu (0x8001)
//   0x3020/1,2 ParkDistanceInformationProvision  front 0x8001, rear 0x8002, status 0x8003
// Control file /tmp/pdc.txt: "<on> <layout> f1..f8 r1..r8 l1..l6 r1..r6"  (on=1 shows the PDC menu).
#include <vsomeip/vsomeip.hpp>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <thread>
#include <vector>

using Bytes = std::vector<vsomeip::byte_t>;
static std::shared_ptr<vsomeip::application> app;
static std::mutex lock;
struct Field { vsomeip::service_t s; vsomeip::instance_t i; vsomeip::event_t e; Bytes v; };
static std::vector<Field> fields;
static std::atomic<unsigned> counter{1};
static std::atomic<bool> registered{false};

static Field *find(vsomeip::service_t s, vsomeip::instance_t i, vsomeip::event_t e) {
    for (auto &f : fields) if (f.s == s && f.i == i && f.e == e) return &f;
    return nullptr;
}
static void set_field(vsomeip::service_t s, vsomeip::instance_t i, vsomeip::event_t e, const Bytes &v) {
    std::lock_guard<std::mutex> g(lock);
    Field *f = find(s, i, e);
    if (!f) return;
    if (f->v == v) return;
    f->v = v;
    if (registered) app->notify(s, i, e, vsomeip::runtime::get()->create_payload(v));
}

static Bytes menu(int on, int layout) {
    unsigned id = counter++ & 0xff;
    // on: HMI_REQUESTED + FADE_IN + VISIBILITY_REQUESTED; off: keep HMI_REQUESTED, FADE_OUT + CLEANUP_REQUESTED
    return {(vsomeip::byte_t)id, 2, (vsomeip::byte_t)(on ? 1 : 2),
            (vsomeip::byte_t)(on ? 1 : 2), (vsomeip::byte_t)layout, 0, 0, 0, 0, 0, 0, 0};
}

static void on_message(const std::shared_ptr<vsomeip::message> &m) {
    std::fprintf(stderr, "call s=%04x i=%04x m=%04x\n", m->get_service(), m->get_instance(), m->get_method());
    if (m->get_message_type() != vsomeip::message_type_e::MT_REQUEST) return;
    std::lock_guard<std::mutex> g(lock);
    // getters: method id = field index (event id - 0x8000) for multi-field services, 0 for the first field
    vsomeip::event_t e = m->get_method() == 0 ? 0x8001 : (vsomeip::event_t)(0x8000 + m->get_method());
    for (auto &f : fields) if (f.s == m->get_service() && f.i == m->get_instance() && f.e == e) {
        auto r = vsomeip::runtime::get()->create_response(m);
        r->set_payload(vsomeip::runtime::get()->create_payload(f.v));
        app->send(r);
        return;
    }
}

int main(int argc, char **argv) {
    const char *control = argc > 1 ? argv[1] : "/tmp/pdc.txt";
    auto add = [](vsomeip::service_t s, vsomeip::instance_t i, vsomeip::event_t e, Bytes v) { fields.push_back({s, i, e, v}); };
    add(0x3059, 1, 0x8001, menu(0, 0));
    for (int inst = 1; inst <= 2; ++inst) {
        add(0x3020, inst, 0x8001, Bytes(8, 255));
        add(0x3020, inst, 0x8002, Bytes(8, 255));
        add(0x3020, inst, 0x8003, {10, 0, 1, 0, 0, 0, 0, 0, 0});   // ACTIVE, obstacles/distances qualifier
    }
    for (int inst = 1; inst <= 2; ++inst) {   // PDCFunctions
        add(0x300e, inst, 0x8002, Bytes(5, 0));
        add(0x300e, inst, 0x8003, Bytes(10, 0));
        add(0x300e, inst, 0x8004, Bytes(8, 0));
        add(0x300e, inst, 0x8005, Bytes(6, 255));
        add(0x300e, inst, 0x8006, Bytes(6, 255));
        add(0x300e, inst, 0x8007, Bytes(12, 0));
    }
    {   // other parking services DidUi/Hmi8 may wait for (fixed-size fields, zeroed)
        struct Z { int s, e, n; };
        const Z zero[] = {{0x3064,0x8001,2},{0x3077,0x8002,1},{0x3077,0x8003,1},{0x3558,0x8001,85},{0x3558,0x8002,7},
            {0x3558,0x8003,61},{0x3558,0x8004,3},{0x3076,0x8001,3},{0x3076,0x8002,1},{0x300c,0x8001,1},{0x300c,0x8002,1},
            {0x300c,0x8003,1},{0x300c,0x8004,1},{0x300c,0x8005,1},{0x300c,0x8006,1},{0x300c,0x8007,1},{0x300c,0x8008,1},
            {0x300c,0x8009,2},{0x300c,0x800a,3},{0x300c,0x800d,2}};
        for (auto &z : zero) add(z.s, 1, z.e, Bytes(z.n, 0));
        add(0x3064, 1, 0x8002, {0, 0});   // ParkmenuRequest {counter, requestParkmenu}
    }
    app = vsomeip::runtime::get()->create_application("parkmenu_fake");
    if (!app->init()) return 1;
    for (vsomeip::service_t s : {0x3059, 0x3020, 0x300e, 0x3064, 0x3077, 0x3558, 0x3076, 0x300c})
        for (vsomeip::instance_t i : {1, 2}) app->register_message_handler(s, i, vsomeip::ANY_METHOD, on_message);
    app->register_state_handler([](vsomeip::state_type_e state) {
        if (state != vsomeip::state_type_e::ST_REGISTERED) return;
        std::lock_guard<std::mutex> g(lock);
        std::set<std::pair<vsomeip::service_t, vsomeip::instance_t>> offered;
        for (auto &f : fields) {
            std::set<vsomeip::eventgroup_t> groups{1, 2, 3, 4};
            app->offer_event(f.s, f.i, f.e, groups, vsomeip::event_type_e::ET_FIELD,
                             std::chrono::milliseconds::zero(), false, true, nullptr,
                             vsomeip::reliability_type_e::RT_UNRELIABLE);
            app->notify(f.s, f.i, f.e, vsomeip::runtime::get()->create_payload(f.v));  // seed the initial value
            offered.insert({f.s, f.i});
        }
        for (auto &p : offered) app->offer_service(p.first, p.second, 1, 0);
        registered = true;
        std::fprintf(stderr, "offered %zu services\n", offered.size());
    });
    std::thread([control] {
        std::string last;
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            std::ifstream in(control);
            std::stringstream text; text << in.rdbuf();
            if (text.str() == last || !registered) continue;
            last = text.str();
            std::vector<int> n; int x; std::istringstream is(last);
            while (is >> x) n.push_back(x);
            if (n.size() < 2) continue;
            int on = n[0], layout = n[1];
            set_field(0x3059, 1, 0x8001, menu(on, layout));
            set_field(0x3064, 1, 0x8002, {(vsomeip::byte_t)(counter & 0xff), (vsomeip::byte_t)(on ? 1 : 0)});
            Bytes front(8, 255), rear(8, 255);
            for (int k = 0; k < 8; ++k) {
                if (n.size() > (size_t)(2 + k)) front[k] = (vsomeip::byte_t)n[2 + k];
                if (n.size() > (size_t)(10 + k)) rear[k] = (vsomeip::byte_t)n[10 + k];
            }
            Bytes left(6, 255), right(6, 255);
            for (int k = 0; k < 6; ++k) {
                if (n.size() > (size_t)(18 + k)) left[k] = (vsomeip::byte_t)n[18 + k];
                if (n.size() > (size_t)(24 + k)) right[k] = (vsomeip::byte_t)n[24 + k];
            }
            for (int inst = 1; inst <= 2; ++inst) {
                set_field(0x3020, inst, 0x8001, front);
                set_field(0x3020, inst, 0x8002, rear);
                set_field(0x300e, inst, 0x8005, left);
                set_field(0x300e, inst, 0x8006, right);
            }
            std::fprintf(stderr, "control: on=%d layout=%d front0=%d rear0=%d\n", on, layout, front[0], rear[0]);
        }
    }).detach();
    std::thread([] {   // re-publish every field once a second so late subscribers get fresh values
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
            if (!registered) continue;
            std::lock_guard<std::mutex> g(lock);
            for (auto &f : fields) app->notify(f.s, f.i, f.e, vsomeip::runtime::get()->create_payload(f.v));
        }
    }).detach();
    app->start();
    return 0;
}
