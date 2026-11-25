CC ?= gcc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11 -D_GNU_SOURCE -fPIE -fstack-protector-strong -D_FORTIFY_SOURCE=2
LDFLAGS ?= -pie -Wl,-z,relro,-z,now

IBUS_CFLAGS := $(shell $(PKG_CONFIG) --cflags ibus-1.0 glib-2.0 gobject-2.0)
IBUS_LIBS   := $(shell $(PKG_CONFIG) --libs ibus-1.0 glib-2.0 gobject-2.0)

TARGET := nido-ibus-vielotus

.PHONY: all clean

all: $(TARGET)

$(TARGET): nido-ibus-vielotus.c
	$(CC) $(CFLAGS) $(IBUS_CFLAGS) -o $@ $< $(IBUS_LIBS) -ldl $(LDFLAGS)

clean:
	rm -f $(TARGET)
