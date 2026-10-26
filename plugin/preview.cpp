// SPDX-License-Identifier: MIT
#include "frame.hpp"
#include <iostream>

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: e16-frame-preview MANIFEST OUTPUT.png [WIDTH HEIGHT] [active|inactive|hover:N|pressed:N] [TITLE]\n";
        return 2;
    }
    try {
        e16::Frame frame(argv[1]);
        int w = argc > 3 ? std::stoi(argv[3]) : 720, h = argc > 4 ? std::stoi(argv[4]) : 380;
        if (w < 1 || h < 1 || w > 8192 || h > 8192) throw std::runtime_error("Preview dimensions must be 1..8192");
        e16::Visual visual;
        if (argc > 5) {
            std::string mode(argv[5]);
            if (mode == "inactive") visual.active = false;
            else if (mode.starts_with("hover:")) visual.hover = std::stoi(mode.substr(6));
            else if (mode.starts_with("pressed:")) visual.pressed = std::stoi(mode.substr(8));
            else if (mode != "active") throw std::runtime_error("Invalid visual state");
        }
        if (argc > 6) visual.title = argv[6];
        auto image = frame.preview(w, h, visual);
        auto status = cairo_surface_write_to_png(image.get(), argv[2]);
        if (status != CAIRO_STATUS_SUCCESS) throw std::runtime_error(cairo_status_to_string(status));
        std::cout << "Rendered " << frame.border << " (" << frame.parts.size() << " parts) to " << argv[2] << '\n';
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << '\n';
        return 1;
    }
}
