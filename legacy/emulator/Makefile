EMULATOR := 1

OBJS += setup.o

ifeq ($(DIY_HAT),1)
OBJS += diy/buttons_evdev.o
OBJS += diy/evdev_keys.o
OBJS += diy/oled_fb.o
else
OBJS += buttons.o
OBJS += oled.o
endif
OBJS += memory.o
OBJS += timer.o
OBJS += udp.o

ifneq ($(filter 1,$(DIY) $(DIY_HAT)),)
OBJS += diy/rand_hw.o
OBJS += diy/hw_salt.o
OBJS += diy/harden.o
endif

OBJS += strl.o

libemulator.a: $(OBJS)
	rm -f $@  # start fresh: objects from a build with other options must not linger
	$(AR) rcs $@ $(OBJS)

include ../Makefile.include

# Remove DIY objects whatever options this clean was run with.
clean::
	rm -f diy/*.o diy/*.d
