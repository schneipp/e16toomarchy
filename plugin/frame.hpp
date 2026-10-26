// SPDX-License-Identifier: MIT
#pragma once
#include <cairo/cairo.h>
#include <json-c/json.h>
#include <pango/pangocairo.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace e16 {
namespace fs = std::filesystem;
using Surface = std::shared_ptr<cairo_surface_t>;
inline Surface surface(cairo_surface_t* ptr) {
    if (cairo_surface_status(ptr) != CAIRO_STATUS_SUCCESS) {
        std::string error = cairo_status_to_string(cairo_surface_status(ptr));
        cairo_surface_destroy(ptr);
        throw std::runtime_error(error);
    }
    return {ptr, cairo_surface_destroy};
}
inline json_object* field(json_object* obj, const char* key) {
    json_object* value = nullptr;
    if (!obj || !json_object_object_get_ex(obj, key, &value)) return nullptr;
    return value;
}
inline std::string str(json_object* obj, const char* key, const std::string& fallback = "") {
    auto value = field(obj, key);
    if (!value) return fallback;
    if (!json_object_is_type(value, json_type_string)) throw std::runtime_error(std::string("Expected string: ") + key);
    return json_object_get_string(value);
}
inline int num(json_object* obj, const char* key, int fallback = 0) {
    auto value = field(obj, key);
    if (!value) return fallback;
    if (!json_object_is_type(value, json_type_int)) throw std::runtime_error(std::string("Expected integer: ") + key);
    auto n = json_object_get_int64(value);
    if (n < -1000000 || n > 1000000) throw std::runtime_error("Geometry value out of range");
    return static_cast<int>(n);
}
inline std::vector<int> ints(json_object* obj, const char* key, size_t length, int fallback = 0) {
    auto arr = field(obj, key);
    if (!arr) return std::vector<int>(length, fallback);
    if (!json_object_is_type(arr, json_type_array) || json_object_array_length(arr) != length)
        throw std::runtime_error(std::string("Invalid array: ") + key);
    std::vector<int> result;
    for (size_t i = 0; i < length; ++i) {
        auto v = json_object_array_get_idx(arr, i);
        if (!json_object_is_type(v, json_type_int) || (json_object_get_int64(v) < -1000000 || json_object_get_int64(v) > 1000000))
            throw std::runtime_error("Invalid numeric array element");
        result.push_back(json_object_get_int(v));
    }
    return result;
}
struct Box {
    double x = 0, y = 0, w = 0, h = 0;
    bool contains(double px, double py) const { return w > 0 && h > 0 && px >= x && py >= y && px < x + w && py < y + h; }
};
struct Anchor { int origin = -1, xp = 0, xa = 0, yp = 0, ya = 0; };
struct State {
    std::string file;
    std::array<int, 4> edges = {};
    bool tile = false;
};
struct ImageClass {
    std::map<std::string, State> states;
    std::array<int, 4> padding = {};
};
struct TextState { std::array<int, 3> color = {230, 230, 230}, background = {0, 0, 0}; std::string effect; };
struct TextClass { int justify = 0; std::map<std::string, TextState> states; };
struct Part {
    std::string image, text, action;
    Anchor tl, br;
    int wmin = 0, wmax = 0, hmin = 0, hmax = 0;
    bool title = false, ontop = true;
};
struct Visual { bool active = true, sticky = false; int hover = -1, pressed = -1; std::string title = "E16 on Omarchy"; };

class Frame {
  public:
    std::string border, font;
    int left = 0, right = 0, top = 0, bottom = 0;
    std::vector<Part> parts;
    std::map<std::string, ImageClass> images;
    std::map<std::string, TextClass> texts;

    explicit Frame(const fs::path& manifest) {
        if (fs::file_size(manifest) > 8 * 1024 * 1024) throw std::runtime_error("Manifest too large");
        root = fs::canonical(manifest).parent_path();
        std::unique_ptr<json_object, decltype(&json_object_put)> doc(json_object_from_file(manifest.c_str()), json_object_put);
        if (!doc || num(doc.get(), "version") != 1) throw std::runtime_error("Invalid decoration manifest/version");
        border = str(doc.get(), "border");
        font = str(doc.get(), "font", "Sans Bold 10");
        auto ext = field(doc.get(), "extents");
        left = num(ext, "left"); right = num(ext, "right"); top = num(ext, "top"); bottom = num(ext, "bottom");
        for (int v : {left, right, top, bottom}) if (v < 0 || v > 512) throw std::runtime_error("Invalid frame extents");
        auto imageObj = field(doc.get(), "images");
        if (!imageObj || !json_object_is_type(imageObj, json_type_object) || json_object_object_length(imageObj) > 2048)
            throw std::runtime_error("Invalid image classes");
        json_object_object_foreach(imageObj, classname, cls) {
            ImageClass image;
            auto padding = ints(cls, "padding", 4);
            std::copy(padding.begin(), padding.end(), image.padding.begin());
            auto states = field(cls, "states");
            if (!states || !json_object_is_type(states, json_type_object)) throw std::runtime_error("Invalid image states");
            json_object_object_foreach(states, stateName, value) {
                State state;
                state.file = str(value, "file");
                auto edges = ints(value, "edges", 4);
                std::copy(edges.begin(), edges.end(), state.edges.begin());
                if (std::ranges::any_of(state.edges, [](int n) { return n < 0 || n > 8192; })) throw std::runtime_error("Invalid image edges");
                state.tile = json_object_get_boolean(field(value, "tile"));
                if (!state.file.empty()) load(state.file);
                image.states[stateName] = state;
            }
            images[classname] = image;
        }
        auto textObj = field(doc.get(), "texts");
        if (textObj && json_object_is_type(textObj, json_type_object)) {
            json_object_object_foreach(textObj, classname, cls) {
                TextClass text;
                text.justify = std::clamp(num(cls, "justify"), 0, 1024);
                auto states = field(cls, "styles");
                if (!states || !json_object_is_type(states, json_type_object)) continue;
                json_object_object_foreach(states, stateName, value) {
                    TextState state;
                    auto color = ints(value, "color", 3, 230), bg = ints(value, "background", 3);
                    std::copy(color.begin(), color.end(), state.color.begin());
                    std::copy(bg.begin(), bg.end(), state.background.begin());
                    state.effect = str(value, "effect");
                    text.states[stateName] = state;
                }
                texts[classname] = text;
            }
        }
        auto partObj = field(doc.get(), "parts");
        if (!partObj || !json_object_is_type(partObj, json_type_array) || json_object_array_length(partObj) > 1024)
            throw std::runtime_error("Invalid border parts");
        for (size_t i = 0; i < json_object_array_length(partObj); ++i) {
            auto value = json_object_array_get_idx(partObj, i);
            Part part;
            part.image = str(value, "image"); part.text = str(value, "text"); part.action = str(value, "action");
            part.title = json_object_get_boolean(field(value, "title"));
            part.ontop = num(value, "ontop", 1);
            auto geom = field(value, "geometry");
            auto anchor = [](json_object* obj) { return Anchor{num(obj, "origin", -1), num(obj, "xp"), num(obj, "xa"), num(obj, "yp"), num(obj, "ya")}; };
            part.tl = anchor(field(geom, "topleft")); part.br = anchor(field(geom, "bottomright"));
            auto w = ints(geom, "width", 2), h = ints(geom, "height", 2);
            part.wmin = w[0]; part.wmax = w[1]; part.hmin = h[0]; part.hmax = h[1];
            parts.push_back(part);
        }
        layout(800, 600, "E16 on Omarchy"); // Validate anchor references/cycles before entering the render loop.
    }

    std::vector<Box> layout(int width, int height, const std::string& title) const {
        if (width < 1 || height < 1 || width > 16384 || height > 16384) throw std::runtime_error("Frame dimensions out of range");
        std::vector<Box> boxes(parts.size());
        std::vector<int> marks(parts.size());
        std::function<void(size_t)> calculate = [&](size_t i) {
            if (marks[i] == 2) return;
            if (marks[i] == 1) throw std::runtime_error("Cyclic E16 border anchors");
            marks[i] = 1;
            auto point = [&](const Anchor& a) -> std::array<double, 2> {
                Box origin{0, 0, static_cast<double>(width), static_cast<double>(height)};
                if (a.origin >= 0) {
                    if (static_cast<size_t>(a.origin) >= parts.size()) throw std::runtime_error("Invalid E16 border anchor");
                    calculate(a.origin); origin = boxes[a.origin];
                }
                return {origin.x + std::floor(a.xp * origin.w / 1024) + a.xa,
                        origin.y + std::floor(a.yp * origin.h / 1024) + a.ya};
            };
            const auto& p = parts[i];
            auto tl = point(p.tl), br = point(p.br);
            Box box{tl[0], tl[1], br[0] - tl[0] + 1, br[1] - tl[1] + 1};
            auto constrain = [](double& start, double& size, double end, int min, int max) {
                if (size > max) { size = max; start = std::floor((start + end - size) / 2); }
                else if (size < min) size = min;
            };
            int hmax = p.hmax, wmax = p.wmax;
            if (p.title && (hmax == 0 || wmax == 0)) {
                auto scratch = surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1));
                auto cr = cairo_create(scratch.get());
                auto pl = pango_cairo_create_layout(cr);
                auto desc = pango_font_description_from_string(font.c_str());
                pango_layout_set_font_description(pl, desc);
                pango_layout_set_text(pl, title.c_str(), -1);
                int tw, th; pango_layout_get_pixel_size(pl, &tw, &th);
                auto image = images.find(p.image);
                std::array<int, 4> padding = image == images.end() ? std::array<int, 4>{} : image->second.padding;
                if (!hmax) hmax = th + padding[2] + padding[3];
                if (!wmax) wmax = tw + padding[0] + padding[1];
                pango_font_description_free(desc); g_object_unref(pl); cairo_destroy(cr);
            }
            constrain(box.y, box.h, br[1], p.hmin, hmax);
            constrain(box.x, box.w, br[0], p.wmin, wmax);
            // E16 can deliberately hide an alternate titlebar using zero-sized parts.
            box.w = std::max(0.0, box.w); box.h = std::max(0.0, box.h);
            boxes[i] = box; marks[i] = 2;
        };
        for (size_t i = 0; i < parts.size(); ++i) calculate(i);
        return boxes;
    }

    const State* stateFor(size_t i, const Visual& v) const {
        auto image = images.find(parts[i].image);
        if (image == images.end()) return nullptr;
        for (const auto& key : stateKeys(i, v)) {
            auto it = image->second.states.find(key);
            if (it != image->second.states.end() && !it->second.file.empty()) return &it->second;
        }
        return nullptr;
    }

    void paint(cairo_t* cr, int width, int height, const Visual& visual) {
        auto boxes = layout(width, height, visual.title);
        // E16 KEEP_ON_TOP parts paint above the lower frame layers.
        for (bool ontop : {false, true}) for (size_t i = 0; i < parts.size(); ++i) {
            if (parts[i].ontop != ontop || boxes[i].w <= 0 || boxes[i].h <= 0) continue;
            if (const auto* state = stateFor(i, visual)) nineSlice(cr, load(state->file), boxes[i], *state);
            if (parts[i].title && texts.contains(parts[i].text)) paintTitle(cr, i, boxes[i], visual);
        }
    }

    Surface preview(int clientWidth, int clientHeight, const Visual& visual) {
        int width = clientWidth + left + right, height = clientHeight + top + bottom;
        auto result = surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32, width, height));
        auto cr = cairo_create(result.get());
        cairo_set_source_rgb(cr, .09, .10, .13);
        cairo_rectangle(cr, left, top, clientWidth, clientHeight); cairo_fill(cr);
        cairo_save(cr); clipFrame(cr, width, height); paint(cr, width, height, visual); cairo_restore(cr);
        cairo_destroy(cr);
        return result;
    }

    void clipFrame(cairo_t* cr, int width, int height) const {
        cairo_rectangle(cr, 0, 0, width, height);
        cairo_rectangle(cr, left, top, std::max(0, width - left - right), std::max(0, height - top - bottom));
        cairo_set_fill_rule(cr, CAIRO_FILL_RULE_EVEN_ODD); cairo_clip(cr);
    }

    int hit(int width, int height, double x, double y, const Visual& visual) {
        if (x >= left && x < width - right && y >= top && y < height - bottom) return -1;
        auto boxes = layout(width, height, visual.title);
        for (bool ontop : {true, false}) for (int i = static_cast<int>(parts.size()) - 1; i >= 0; --i) {
            if (parts[i].ontop != ontop || !boxes[i].contains(x, y)) continue;
            const auto* state = stateFor(i, visual);
            if (!state) continue;
            // Respect shape-mask transparency, including nine-slice coordinates.
            auto src = load(state->file);
            int sw = cairo_image_surface_get_width(src.get()), sh = cairo_image_surface_get_height(src.get());
            auto coord = [](double p, double dest, int size, int lo, int hi, bool tile) {
                lo = std::min(lo, size / 2); hi = std::min(hi, size - lo);
                double dl = std::min<double>(lo, dest / 2), dh = std::min<double>(hi, dest - dl);
                if (p < dl && dl > 0) return static_cast<int>(p * lo / dl);
                if (p >= dest - dh && dh > 0) return size - hi + static_cast<int>((p - dest + dh) * hi / dh);
                int span = std::max(1, size - lo - hi);
                return lo + (tile ? static_cast<int>(p - dl) % span : static_cast<int>((p - dl) * span / std::max(1.0, dest - dl - dh)));
            };
            int sx = std::clamp(coord(x - boxes[i].x, boxes[i].w, sw, state->edges[0], state->edges[1], state->tile), 0, sw - 1);
            int sy = std::clamp(coord(y - boxes[i].y, boxes[i].h, sh, state->edges[2], state->edges[3], state->tile), 0, sh - 1);
            cairo_surface_flush(src.get());
            auto data = reinterpret_cast<uint32_t*>(cairo_image_surface_get_data(src.get()) + sy * cairo_image_surface_get_stride(src.get()));
            if ((data[sx] >> 24) >= 32) return i;
        }
        return -1;
    }

  private:
    fs::path root;
    std::map<std::string, Surface> surfaces;
    std::map<std::string, Surface> patches;

    Surface load(const std::string& file) {
        if (surfaces.contains(file)) return surfaces.at(file);
        fs::path rel(file);
        if (rel.is_absolute()) throw std::runtime_error("Absolute asset path rejected");
        auto path = fs::canonical(root / rel);
        auto relative = path.lexically_relative(root);
        if (relative.empty() || *relative.begin() == "..") throw std::runtime_error("Asset path outside theme rejected");
        auto image = surface(cairo_image_surface_create_from_png(path.c_str()));
        auto w = cairo_image_surface_get_width(image.get()), h = cairo_image_surface_get_height(image.get());
        if (w < 1 || h < 1 || static_cast<int64_t>(w) * h > 16 * 1024 * 1024) throw std::runtime_error("Asset dimensions out of range");
        surfaces[file] = image;
        return image;
    }
    static std::vector<std::string> stateKeys(size_t i, const Visual& v) {
        std::vector<std::string> keys;
        std::string base = v.pressed == static_cast<int>(i) ? "clicked" : v.hover == static_cast<int>(i) ? "hilited" : "normal";
        for (auto b : {base, std::string("normal")}) {
            if (v.active && v.sticky) keys.push_back(b + "_active_sticky");
            if (v.active) { keys.push_back(b + "_active"); if (b != "normal") keys.push_back("normal_active_" + b); }
            if (v.sticky) keys.push_back(b + "_sticky");
            keys.push_back(b);
        }
        return keys;
    }
    void nineSlice(cairo_t* cr, Surface source, const Box& box, const State& state) {
        const int w = cairo_image_surface_get_width(source.get()), h = cairo_image_surface_get_height(source.get());
        int l = std::min(state.edges[0], w / 2), r = std::min(state.edges[1], w - l);
        int t = std::min(state.edges[2], h / 2), b = std::min(state.edges[3], h - t);
        double dl = std::min<double>(l, box.w / 2), dr = std::min<double>(r, box.w - dl);
        double dt = std::min<double>(t, box.h / 2), db = std::min<double>(b, box.h - dt);
        int sx[] = {0, l, w - r, w}, sy[] = {0, t, h - b, h};
        double dx[] = {box.x, box.x + dl, box.x + box.w - dr, box.x + box.w};
        double dy[] = {box.y, box.y + dt, box.y + box.h - db, box.y + box.h};
        for (int row = 0; row < 3; ++row) for (int col = 0; col < 3; ++col) {
            int pw = sx[col + 1] - sx[col], ph = sy[row + 1] - sy[row];
            double dw = dx[col + 1] - dx[col], dh = dy[row + 1] - dy[row];
            if (pw <= 0 || ph <= 0 || dw <= 0 || dh <= 0) continue;
            auto key = state.file + ":" + std::to_string(row) + ":" + std::to_string(col) + ":" + std::to_string(l) + ":" + std::to_string(r) + ":" + std::to_string(t) + ":" + std::to_string(b);
            if (!patches.contains(key)) {
                auto patch = surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32, pw, ph));
                auto pcr = cairo_create(patch.get());
                cairo_set_source_surface(pcr, source.get(), -sx[col], -sy[row]); cairo_paint(pcr); cairo_destroy(pcr);
                patches[key] = patch;
            }
            cairo_save(cr);
            cairo_rectangle(cr, dx[col], dy[row], dw, dh); cairo_clip(cr);
            cairo_translate(cr, dx[col], dy[row]);
            bool tileX = state.tile && col == 1, tileY = state.tile && row == 1;
            cairo_scale(cr, tileX ? 1 : dw / pw, tileY ? 1 : dh / ph);
            cairo_set_source_surface(cr, patches.at(key).get(), 0, 0);
            cairo_pattern_set_extend(cairo_get_source(cr), CAIRO_EXTEND_REPEAT);
            cairo_pattern_set_filter(cairo_get_source(cr), CAIRO_FILTER_NEAREST);
            cairo_paint(cr); cairo_restore(cr);
        }
    }
    void paintTitle(cairo_t* cr, size_t i, Box box, const Visual& visual) {
        const auto& text = texts.at(parts[i].text);
        TextState state;
        for (const auto& key : stateKeys(i, visual)) if (text.states.contains(key)) { state = text.states.at(key); break; }
        if (images.contains(parts[i].image)) {
            auto pad = images.at(parts[i].image).padding;
            box.x += pad[0]; box.y += pad[2]; box.w -= pad[0] + pad[1]; box.h -= pad[2] + pad[3];
        }
        if (box.w < 1 || box.h < 1) return;
        cairo_save(cr); cairo_rectangle(cr, box.x, box.y, box.w, box.h); cairo_clip(cr);
        auto layout = pango_cairo_create_layout(cr);
        auto desc = pango_font_description_from_string(font.c_str());
        pango_layout_set_font_description(layout, desc);
        pango_layout_set_text(layout, visual.title.c_str(), -1);
        pango_layout_set_single_paragraph_mode(layout, true);
        pango_layout_set_width(layout, static_cast<int>(box.w * PANGO_SCALE));
        pango_layout_set_ellipsize(layout, PANGO_ELLIPSIZE_END);
        pango_layout_set_alignment(layout, text.justify < 256 ? PANGO_ALIGN_LEFT : text.justify > 768 ? PANGO_ALIGN_RIGHT : PANGO_ALIGN_CENTER);
        int tw, th; pango_layout_get_pixel_size(layout, &tw, &th);
        auto draw = [&](double dx, double dy, std::array<int, 3> color) {
            cairo_set_source_rgb(cr, color[0] / 255.0, color[1] / 255.0, color[2] / 255.0);
            cairo_move_to(cr, box.x + dx, box.y + std::floor((box.h - th) / 2) + dy); pango_cairo_show_layout(cr, layout);
        };
        if (state.effect == "shadow") draw(1, 1, state.background);
        if (state.effect == "outline") for (int y = -1; y <= 1; ++y) for (int x = -1; x <= 1; ++x) if (x || y) draw(x, y, state.background);
        draw(0, 0, state.color);
        pango_font_description_free(desc); g_object_unref(layout); cairo_restore(cr);
    }
};
} // namespace e16
