# Copyright 2026 Paul Maddern (paul@arcadegeek.co.uk)
SNAPSHOT = TheWildBunch.z80
T2S = thewildbunch.t2s

MK = $(SKOOLKIT_HOME)/tools/disassembly.mk
ifeq ($(wildcard $(MK)),)
	$(error $(MK): file not found)
endif
include $(MK)

# Prerequisite-only: make merges this with disassembly.mk's html recipe
# without an override warning. graphics runs first and leaves PNGs in place.
# skool2html does not delete existing files under images/.
html: graphics

.PHONY: graphics
graphics: snapshot
	mkdir -p $(BUILD)/html/thewildbunch/images
	python3 utils/render_wildbunch_graphics.py $(SNAPSHOT) \
	  $(BUILD)/html/thewildbunch/images
