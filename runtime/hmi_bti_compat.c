#define _GNU_SOURCE
#include <asm/hwcap.h>
#include <dlfcn.h>
#include <link.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/auxv.h>
#include <sys/mman.h>
#include <unistd.h>

static int relax_hmi_bti(struct dl_phdr_info *image, size_t size, void *context) {
    (void)size;
    const Dl_info *hmi = context;
    if (image->dlpi_addr != (uintptr_t)hmi->dli_fbase) return 0;
    uintptr_t page_size = (uintptr_t)sysconf(_SC_PAGESIZE);
    for (size_t index = 0; index < image->dlpi_phnum; ++index) {
        const ElfW(Phdr) *segment = &image->dlpi_phdr[index];
        if (segment->p_type != PT_LOAD || !(segment->p_flags & PF_X) || !segment->p_memsz) continue;
        uintptr_t start = (image->dlpi_addr + segment->p_vaddr) & ~(page_size - 1);
        uintptr_t end = (image->dlpi_addr + segment->p_vaddr + segment->p_memsz + page_size - 1) & ~(page_size - 1);
        int protection = PROT_EXEC;
        if (segment->p_flags & PF_R) protection |= PROT_READ;
        if (segment->p_flags & PF_W) protection |= PROT_WRITE;
        if (mprotect((void *)start, end - start, protection)) {
            perror("hmi-compat: cannot relax Hmi8 BTI protection");
            _exit(1);
        }
    }
    fprintf(stderr, "hmi-compat: disabled BTI for legacy Hmi8 executable mappings\n");
    return 1;
}

__attribute__((constructor)) static void initialize(void) {
    if (!(getauxval(AT_HWCAP2) & HWCAP2_BTI)) return;
    void *symbol = dlsym(RTLD_DEFAULT, "_ZN12CHmiProcUnit12dispatchCallESt8functionIFvvEESt10shared_ptrI29CHmiDispatchedCallEventAnchorE");
    Dl_info hmi;
    if (!symbol || !dladdr(symbol, &hmi)) {
        // This CPU enforces BTI, but the Hmi8 symbol used to find its executable mappings is
        // absent (different Hmi8 build?). Without the workaround Hmi8 typically dies with SIGILL.
        fprintf(stderr, "hmi-compat: WARNING: BTI is enforced but the Hmi8 anchor symbol was not found; "
                        "BTI workaround NOT applied (expect SIGILL / exit 132 on this Hmi8 build)\n");
        return;
    }
    if (!dl_iterate_phdr(relax_hmi_bti, &hmi))
        fprintf(stderr, "hmi-compat: WARNING: BTI is enforced but no Hmi8 executable mapping matched; "
                        "BTI workaround NOT applied\n");
}
