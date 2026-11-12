// DidUi-only compatibility adapter for Ramses 27.0.130 (hash gated by pdc_sim.py).
// API layouts/signatures checked against the matching public Ramses release.
// Keep the native geometry, shaders, textures and distance controllers intact.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <unordered_set>

template<typename T> static T next_symbol(const char *name) {
    return reinterpret_cast<T>(dlsym(RTLD_NEXT, name));
}

extern "C" int _ZN6ramses5Scene5flushEm(void *scene, unsigned long version) {
    using Flush = int (*)(void *, unsigned long);
    static auto real = next_symbol<Flush>("_ZN6ramses5Scene5flushEm");
    static auto id = next_symbol<unsigned long (*)(const void *)>("_ZNK6ramses5Scene10getSceneIdEv");
    static auto find = next_symbol<void *(*)(void *, const char *)>("_ZN6ramses5Scene16findObjectByNameEPKc");
    static auto target = next_symbol<int (*)(void *, void *)>("_ZN6ramses10RenderPass15setRenderTargetEPNS_12RenderTargetE");
    static auto enabled = next_symbol<int (*)(void *, bool)>("_ZN6ramses10RenderPass10setEnabledEb");
    static std::unordered_set<void *> configured;
    if (id && find && target && enabled && id(scene) == 43300803 && !configured.count(scene)) {
        void *main = find(scene, "RenderPass_default");
        void *copy = find(scene, "offscreenbuffer render pass");
        if (main && copy && target(main, nullptr) == 0 && enabled(copy, false) == 0) {
            // The intermediate offscreen path loses this 3-D pass here. Draw it
            // straight into the scene output; retain the later native UI pass.
            configured.insert(scene);
            fprintf(stderr, "pdc-view: native CID parking render bypass enabled\n");
        }
    }
    return real(scene, version);
}

extern "C" int _ZN6ramses19DcsmMetadataCreator15setCarModelViewERKNS_20CarModelViewMetadataERKNS_20AnimationInformationE(
    void *creator, const float *view, const void *timing) {
    using SetView = int (*)(void *, const float *, const void *);
    static auto real = next_symbol<SetView>("_ZN6ramses19DcsmMetadataCreator15setCarModelViewERKNS_20CarModelViewMetadataERKNS_20AnimationInformationE");
    // CarModelViewMetadata: pitch, yaw, distance, origin xyz, FOV, near, far.
    std::array<float, 9> copy;
    std::memcpy(copy.data(), view, sizeof copy);
    static const float zoom = [] {
        const char *value = std::getenv("PDC_CAR_ZOOM");
        float parsed = value ? std::strtof(value, nullptr) : 1.25f;
        return std::isfinite(parsed) ? std::clamp(parsed, 1.0f, 1.5f) : 1.25f;
    }();
    if (copy[0] > 89.0f && copy[0] < 91.0f && copy[2] > 100.0f) {
        // Hmi8 provides this same camera transform to DidUi, so the vehicle and
        // sensor zones stay aligned. Do not scale the vehicle mesh on its own.
        copy[2] /= zoom;
    }
    return real(creator, copy.data(), timing);
}
