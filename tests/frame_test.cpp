// SPDX-License-Identifier: MIT
#include "../plugin/frame.hpp"
#include <cassert>
#include <iostream>
int main(int argc, char** argv) {
    assert(argc == 2);
    e16::Frame frame(argv[1]);
    e16::Visual visual;
    auto boxes = frame.layout(104, 74, "Fixture");
    assert(boxes.size() == 1);
    assert(boxes[0].x == 0 && boxes[0].y == 0 && boxes[0].w == 104 && boxes[0].h == 12);
    assert(frame.hit(104, 74, 52, 6, visual) == 0);
    assert(frame.hit(104, 74, 0, 0, visual) == -1); // Transparent shape mask.
    assert(frame.hit(104, 74, 52, 20, visual) == -1); // Client surface.
    assert(frame.hit(104, 74, 200, 6, visual) == -1);
    // A forward anchor resolves against a later part, independent of paint order.
    auto child = frame.parts[0];
    child.tl = {1, 1024, -10, 0, 2}; child.br = {1, 1024, -1, 0, 9};
    child.wmin = child.wmax = 10; child.hmin = child.hmax = 8;
    frame.parts.insert(frame.parts.begin(), child);
    boxes = frame.layout(104, 74, "Fixture");
    assert(boxes[0].x == 94 && boxes[0].y == 2 && boxes[0].w == 10 && boxes[0].h == 8);
    // Cycles are rejected before recursive geometry can exhaust the stack.
    frame.parts[1].tl.origin = 0;
    bool rejected = false;
    try { frame.layout(104, 74, "Fixture"); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    std::cout << "Geometry, forward anchors and alpha hit tests passed\n";
}
