/*
 * DIY build: keep the wallet's memory where only the wallet can see it.
 *
 *   - No core dumps, and no other process of the same user may attach a
 *     debugger (PR_SET_DUMPABLE 0). A crash must never write keys to the card.
 *   - All memory locked in RAM (mlockall), so it is never written to swap on
 *     the memory card. diywallet.service raises LimitMEMLOCK for this;
 *     diywallet-check.service switches swap off before every start.
 *
 * Licensed like the rest of the emulator: GNU LGPL v3 or later.
 */

#include <stdio.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>

void diy_harden(void) {
  struct rlimit no_core = {0, 0};
  setrlimit(RLIMIT_CORE, &no_core);
  if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) {
    perror("prctl(PR_SET_DUMPABLE)");
  }
  if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
    perror("Could not lock memory in RAM (mlockall); make sure swap is off");
  }
}

/* Marker that install.sh checks, so a test build with the debug interface
 * (which can read out the recovery phrase) is never installed as a wallet,
 * even if the program file was stripped of its symbol names. */
#if DEBUG_LINK
__attribute__((used)) const char diy_build_kind[] = "DIY-BUILD-KIND:TEST";
#else
__attribute__((used)) const char diy_build_kind[] = "DIY-BUILD-KIND:WALLET";
#endif
