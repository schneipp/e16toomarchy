CXX ?= c++
PYTHON ?= .venv/bin/python
CXXFLAGS ?= -O2 -g -Wall -Wextra
FRAME_FLAGS := $(shell pkg-config --cflags --libs cairo pangocairo json-c)
HYPR_FLAGS := $(shell pkg-config --cflags hyprland 2>/dev/null)

.PHONY: all plugin preview test clean
all: preview plugin
preview: build/e16-frame-preview
plugin: build/e16-decorations.so

build:
	mkdir -p build

build/e16-frame-preview: plugin/preview.cpp plugin/frame.hpp | build
	$(CXX) $(CXXFLAGS) -std=c++23 plugin/preview.cpp -o $@ $(FRAME_FLAGS)

build/e16-decorations.so: plugin/plugin.cpp plugin/frame.hpp | build
	$(CXX) $(CXXFLAGS) -std=c++23 -fPIC -shared -fno-gnu-unique plugin/plugin.cpp -o $@.tmp $(HYPR_FLAGS) $(FRAME_FLAGS)
	mv $@.tmp $@

build/e16-frame-test: tests/frame_test.cpp plugin/frame.hpp | build
	$(CXX) $(CXXFLAGS) -std=c++23 tests/frame_test.cpp -o $@ $(FRAME_FLAGS)

test: preview build/e16-frame-test
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf build
