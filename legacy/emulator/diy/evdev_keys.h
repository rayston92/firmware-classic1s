/* DIY build: logical keys read from Linux input devices. */
#ifndef DIY_EVDEV_KEYS_H
#define DIY_EVDEV_KEYS_H

#define DIY_KEY_YES 1u
#define DIY_KEY_NO 2u
#define DIY_KEY_UP 4u
#define DIY_KEY_DOWN 8u

/* Bit mask of DIY_KEY_* currently held down. Never blocks. */
unsigned diy_keys_pressed(void);

#endif
