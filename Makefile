# Pocket Adventure FX – build
#
# Needs at least your own copy of the VGA floppy version (DISK01.LEC …);
# every further language version is added to the game as a language:
#   make ORIGINALS="/path/to/MONKEY /path/to/MONKEY-DE"
# or permanently in config.mk (not in the repo):
#   ORIGINALS := /path/to/MONKEY /path/to/MONKEY-DE
# Graphics, walk paths and music come from the first copy.
#
#   make            build data + sketch
#   make package    plus the .arduboy package (dist/PocketAdventureFX.arduboy)
#   make data       FX data only (game.adv → game.bin, gamedata.h, fxdata.h)
#   make art        regenerate placeholder graphics (does not overwrite)
#   make test       compiler tests and scene runs on the host test bench
#   make fxhost     build only the host test bench (build/fxhost)
#   make upload     sketch + FX development data to the Arduboy FX
#   make TIMING=1 … with timing per layer bottom right (µs, development)
#   make clean

-include config.mk

FQBN        := arduboy-homemade:avr:arduboy-fx
# build.sh puts a locally installed arduino-cli with its own configuration here.
ARDUINO_CLI ?= arduino-cli
PORT        ?= $(shell ls /dev/cu.usbmodem* 2>/dev/null | head -1)
PYTHON      := .venv/bin/python
SKETCH      := PocketAdventureFX
FXDIR       := $(SKETCH)/fxdata
BUILD       := build

SOURCES     := $(wildcard $(SKETCH)/*.cpp $(SKETCH)/*.h $(SKETCH)/*.ino)
# Bundled libraries (ArduboyG: greyscale; libraries/README.md)
LIBRARIES   := libraries/ArduboyG
LIB_SOURCES := $(shell find $(LIBRARIES) -type f)
ASSETS      := game/game.adv $(shell find game/art -type f)
# Earlier variables (one copy per language) still work.
ORIGINALS   ?= $(strip $(ORIGINAL) $(ORIGINAL_DE))
ORIGINAL_FILES := $(foreach d,$(ORIGINALS),$(wildcard $(d)/DISK*.LEC))

# Which copies were built: if the selection changes (also behind the
# links from build.sh), it must be recompiled even though no file
# is newer. The stamp is only written on change.
ORIGINALS_STAMP := $(BUILD)/originals.txt
ORIGINALS_REAL := $(foreach d,$(ORIGINALS),$(realpath $(d)))
$(shell mkdir -p $(BUILD); echo '$(ORIGINALS_REAL)' | cmp -s - $(ORIGINALS_STAMP) || echo '$(ORIGINALS_REAL)' > $(ORIGINALS_STAMP))

PACKAGE     := dist/PocketAdventureFX.arduboy

.PHONY: all data art test fxhost package upload upload-sketch upload-data clean

all: $(BUILD)/$(SKETCH).ino.hex

$(PYTHON):
	python3 -m venv .venv
	$(PYTHON) -m pip install -q -r tools/requirements.txt

data: $(FXDIR)/fxdata.h

$(FXDIR)/game.bin $(SKETCH)/gamedata.h &: $(ASSETS) $(ORIGINAL_FILES) $(ORIGINALS_STAMP) $(wildcard tools/*.py) | $(PYTHON)
	@test -n "$(ORIGINALS)" || (echo "ORIGINALS missing: make ORIGINALS=/path/to/MONKEY (or set it in config.mk)"; exit 1)
	$(PYTHON) tools/advc.py game/game.adv $(foreach d,$(ORIGINALS),--original "$(d)") --preview $(BUILD)/preview \
	  --bin $(FXDIR)/game.bin --header $(SKETCH)/gamedata.h

$(FXDIR)/fxdata.h: $(FXDIR)/fxdata.txt $(FXDIR)/game.bin | $(PYTHON)
	$(PYTHON) tools/vendor/fxdata-build.py $(FXDIR)/fxdata.txt >/dev/null

# make TIMING=1: timing per layer for development (PocketAdventureFX.ino).
# If the setting changes, it must be recompiled (stamp as above).
TIMING_FLAGS := $(if $(TIMING),--build-property compiler.cpp.extra_flags=-DRENDER_TIMING)
TIMING_STAMP := $(BUILD)/timing.txt
$(shell echo '$(TIMING)' | cmp -s - $(TIMING_STAMP) || echo '$(TIMING)' > $(TIMING_STAMP))

$(BUILD)/$(SKETCH).ino.hex: $(FXDIR)/fxdata.h $(SKETCH)/gamedata.h $(SOURCES) $(LIB_SOURCES) $(TIMING_STAMP)
	$(ARDUINO_CLI) compile --fqbn $(FQBN) --warnings all $(addprefix --library ,$(LIBRARIES)) \
	  $(TIMING_FLAGS) --output-dir $(BUILD) $(SKETCH)

package: $(PACKAGE)

$(PACKAGE): $(BUILD)/$(SKETCH).ino.hex $(FXDIR)/fxdata.h tools/package.py | $(PYTHON)
	$(PYTHON) tools/package.py --hex $(BUILD)/$(SKETCH).ino.hex --data $(FXDIR)/fxdata-data.bin \
	  --cart $(BUILD)/preview/title.png --game $(FXDIR)/game.bin --out $@

# Host test bench: the engine sources compiled against a replica of Arduboy2 and
# ArduboyFX (tests/fxhost). Ui.cpp includes harness.cpp itself.
# The font comes from the Arduboy2 library that build.sh sets up.
FXHOST      := $(BUILD)/fxhost
FXHOST_SRC  := $(SKETCH)/World.cpp $(SKETCH)/Script.cpp $(SKETCH)/Display.cpp tests/fxhost/harness.cpp
FXHOST_FONT := $(BUILD)/fxhost_font.cpp
ARDUBOY2_DATA := $(firstword $(shell find $(BUILD)/arduino $(HOME)/Library/Arduino15 $(HOME)/.arduino15 \
                   -path '*Arduboy2/src/Arduboy2Data.cpp' 2>/dev/null))
HOSTCXX     ?= c++
HOSTFLAGS   := -std=c++17 -O1 -g -Wall -Wno-unused-function -Wno-pragma-pack -include tests/fxhost/prelude.h -Itests/fxhost/shim

fxhost: $(FXHOST)

$(FXHOST_FONT):
	@test -n "$(ARDUBOY2_DATA)" || (echo "Arduboy2 library not found: run ./build.sh first"; exit 1)
	{ echo '#include <cstdint>'; echo 'namespace host { extern const uint8_t font5x7[] = {'; \
	  sed -n '/font5x7\[\] = {/,/^};/p' "$(ARDUBOY2_DATA)" | sed '1d;$$d'; echo '}; }'; } > $@

$(FXHOST): $(FXHOST_SRC) $(FXHOST_FONT) $(SOURCES) $(wildcard tests/fxhost/*.h tests/fxhost/shim/*.h) $(FXDIR)/fxdata.h $(SKETCH)/gamedata.h
	$(HOSTCXX) $(HOSTFLAGS) -o $@ $(FXHOST_SRC) -x c++ $(SKETCH)/$(SKETCH).ino -x none $(FXHOST_FONT) -lz

art: | $(PYTHON)
	$(PYTHON) tools/draw_art.py

test: $(FXHOST) | $(PYTHON)
	$(PYTHON) -m unittest discover -s tests -v

upload: upload-sketch upload-data

upload-sketch: $(BUILD)/$(SKETCH).ino.hex
	@test -n "$(PORT)" || (echo "No Arduboy found (/dev/cu.usbmodem*). Is it switched on?"; exit 1)
	$(ARDUINO_CLI) upload --fqbn $(FQBN) --port $(PORT) --input-dir $(BUILD) $(SKETCH)

upload-data: $(FXDIR)/fxdata.h
	$(PYTHON) tools/vendor/fxdata-upload.py $(FXDIR)/fxdata.bin

clean:
	rm -rf $(BUILD) dist $(FXDIR)/game.bin $(FXDIR)/fxdata.h $(FXDIR)/fxdata.bin $(FXDIR)/fxdata-data.bin $(SKETCH)/gamedata.h
