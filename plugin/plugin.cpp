// SPDX-License-Identifier: MIT
// Hyprland 0.56.2 plugin. Rebuild for the exact compositor ABI before loading.
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/config/values/types/StringValue.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/layout/LayoutManager.hpp>
#include <hyprland/src/managers/fullscreen/FullscreenController.hpp>
#include <hyprland/src/pointer/PointerManager.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopManager.hpp>
extern "C" {
#include <lua.h>
#include <lauxlib.h>
}
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/Texture.hpp>
#include <hyprland/src/render/pass/TexPassElement.hpp>
#include "frame.hpp"
#include <chrono>
#include <cstdlib>
#include <format>
#include <set>
#include <sstream>

namespace {
HANDLE handle = nullptr;
std::shared_ptr<e16::Frame> frame;
SP<Config::Values::CStringValue> themeOption;
std::vector<CHyprSignalListener> listeners;
SP<CEventLoopTimer> themeTimer;
std::string overridePath, lastStamp;
class Decoration;
std::set<Decoration*> decorations;

std::filesystem::path manifestPath() {
    std::string path = overridePath.empty() ? themeOption->value() : overridePath;
    if (path.empty()) {
        const char* config = std::getenv("XDG_CONFIG_HOME");
        const char* home = std::getenv("HOME");
        if (!config && !home) throw std::runtime_error("HOME and XDG_CONFIG_HOME are unset");
        const char* state = std::getenv("XDG_STATE_HOME");
        auto stateTheme = (state ? std::filesystem::path(state) : std::filesystem::path(home ? home : "") / ".local/state") / "omarchy/current/theme";
        auto legacyTheme = (config ? std::filesystem::path(config) : std::filesystem::path(home) / ".config") / "omarchy/current/theme";
        return (std::filesystem::exists(stateTheme) ? stateTheme : legacyTheme) / "e16/decoration.json";
    }
    if (path.starts_with("~/")) {
        const char* home = std::getenv("HOME");
        if (!home) throw std::runtime_error("HOME is unset");
        path = std::string(home) + path.substr(1);
    }
    auto result = std::filesystem::path(path);
    if (std::filesystem::is_directory(result)) result /= "decoration.json";
    return result;
}

class Decoration final : public IHyprWindowDecoration {
  public:
    explicit Decoration(PHLWINDOW w) : IHyprWindowDecoration(w), window(w) { decorations.insert(this); }
    ~Decoration() override { decorations.erase(this); }
    SDecorationPositioningInfo getPositioningInfo() override {
        SDecorationPositioningInfo info;
        // 0.56.2 supports four sticky edges and supplies an input box for them.
        info.policy = DECORATION_POSITION_STICKY;
        info.edges = DECORATION_EDGE_TOP | DECORATION_EDGE_BOTTOM | DECORATION_EDGE_LEFT | DECORATION_EDGE_RIGHT;
        info.reserved = true;
        if (visible()) info.desiredExtents = {{static_cast<double>(frame->left), static_cast<double>(frame->top)},
                                             {static_cast<double>(frame->right), static_cast<double>(frame->bottom)}};
        return info;
    }
    void onPositioningReply(const SDecorationPositioningReply&) override {}
    eDecorationType getDecorationType() override { return DECORATION_CUSTOM; }
    eDecorationLayer getDecorationLayer() override { return DECORATION_LAYER_OVER; }
    uint64_t getDecorationFlags() override { return DECORATION_ALLOWS_MOUSE_INPUT | DECORATION_PART_OF_MAIN_WINDOW; }
    std::string getDisplayName() override { return "E16 bitmap frame"; }
    void updateWindow(PHLWINDOW) override {
        bool nowVisible = visible();
        if (nowVisible != wasVisible) { wasVisible = nowVisible; g_pDecorationPositioner->repositionDeco(this); }
        damageEntire();
    }
    void invalidate() { cacheKey.clear(); hover = pressed = -1; g_pDecorationPositioner->repositionDeco(this); damageEntire(); }
    bool visible() const {
        auto w = window.lock();
        return frame && w && w->m_isMapped && !w->isHidden() && !w->m_X11DoesntWantBorders &&
               w->m_ruleApplicator->decorate().valueOrDefault() &&
               !Fullscreen::controller()->isFullscreen(w, Fullscreen::FSMODE_FULLSCREEN);
    }
    CBox globalBox() const {
        auto w = window.lock();
        if (!w || !frame) return {};
        auto pos = w->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
        auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
        if (w->m_workspace && !w->m_pinned) pos += w->m_workspace->m_renderOffset->value();
        pos += w->m_floatingOffset;
        return {pos.x - frame->left, pos.y - frame->top, size.x + frame->left + frame->right, size.y + frame->top + frame->bottom};
    }
    void damageEntire() override {
        if (!window || !g_pHyprRenderer) return;
        if (frame) g_pHyprRenderer->damageBox(globalBox());
    }
    e16::Visual visual() const {
        auto w = window.lock();
        return {Desktop::focusState()->window() == w, w && w->m_pinned, hover, pressed, w ? w->m_title : ""};
    }
    int hit(const Vector2D& pos) {
        if (!visible()) return -1;
        auto box = globalBox();
        if (!box.containsPoint(pos)) return -1;
        return frame->hit(std::lround(box.w), std::lround(box.h), pos.x - box.x, pos.y - box.y, visual());
    }
    void mouseMove(const Vector2D& pos) {
        int next = hit(pos);
        if (next != hover) { hover = next; damageEntire(); }
    }
    bool release(const Vector2D& pos) {
        if (pressed < 0) return false;
        int original = pressed;
        pressed = -1;
        damageEntire();
        if (!visible() || hit(pos) != original) return true;
        auto action = frame->parts.at(original).action;
        auto w = window.lock();
        if (!w) return true;
        if (action == "close") g_pXWaylandManager->sendCloseWindow(w);
        else if (action == "maximize") {
            auto next = Fullscreen::controller()->getFullscreenModes(w).internal == Fullscreen::FSMODE_MAXIMIZED
                            ? Fullscreen::FSMODE_NONE : Fullscreen::FSMODE_MAXIMIZED;
            Fullscreen::controller()->setFullscreenMode(w, next, next);
        } else if (action == "minimize") {
            std::ostringstream address; address << "special:e16-minimized,address:0x" << std::hex << reinterpret_cast<uintptr_t>(w.get());
            g_pKeybindManager->m_dispatchers.at("movetoworkspacesilent")(address.str());
        } else if (action == "pin") {
            // Hyprland pins floating windows; tiled windows must remain in the layout.
            if (w->m_isFloating) g_pKeybindManager->m_dispatchers.at("pin")("active");
        }
        return true;
    }
    void pushStatus(lua_State* L) {
        lua_newtable(L);
        auto box = globalBox();
        auto native = g_pDecorationPositioner->getWindowDecorationBox(this);
        auto set = [&](const char* key, double value) { lua_pushnumber(L, value); lua_setfield(L, -2, key); };
        set("x", box.x); set("y", box.y); set("width", box.w); set("height", box.h);
        set("native_x", native.x); set("native_y", native.y); set("native_w", native.w); set("native_h", native.h);
        set("hover", hover); set("pressed", pressed); set("input_events", inputEvents);
        set("hit", hit(Pointer::mgr()->position()));
    }
    bool onInputOnDeco(eInputType type, const Vector2D& pos, std::any data = {}) override {
        try {
        ++inputEvents;
        auto w = window.lock();
        int index = hit(pos);
        if (index < 0 || !w) return false;
        const auto& action = frame->parts.at(index).action;
        if (type == INPUT_TYPE_DRAG_START) {
            if (action != "move") return false;
            g_layoutManager->dragController()->dragBegin(w->layoutTarget(), MBIND_MOVE);
            return true;
        }
        if (type != INPUT_TYPE_BUTTON) return false;
        auto event = std::any_cast<IPointer::SButtonEvent>(&data);
        if (!event || event->button != 272) return false;
        if (event->state == WL_POINTER_BUTTON_STATE_RELEASED) return release(pos);
        Desktop::focusState()->fullWindowFocus(w, Desktop::FOCUS_REASON_CLICK);
        if (w->m_isFloating) Desktop::windowState()->raise(w);
        if (action == "move" || action.starts_with("resize")) {
            if (action == "move") g_layoutManager->dragController()->dragBegin(w->layoutTarget(), MBIND_MOVE);
            else {
                auto box = globalBox();
                auto corner = Layout::cornerFromBox(box, pos);
                if (action == "resize_h") corner = pos.x < box.middle().x ? Layout::CORNER_LEFT : Layout::CORNER_RIGHT;
                if (action == "resize_v") corner = pos.y < box.middle().y ? Layout::CORNER_TOP : Layout::CORNER_BOTTOM;
                g_layoutManager->dragController()->dragBegin(w->layoutTarget(), MBIND_RESIZE, corner);
            }
        } else if (!action.empty()) { pressed = index; damageEntire(); }
        return true;
        } catch (const std::exception&) { return false; }
    }
    void draw(PHLMONITOR monitor, float const& alpha) override {
        if (!visible()) return;
        try {
            auto box = globalBox();
            const int width = std::lround(box.w), height = std::lround(box.h);
            auto v = visual();
            const double scale = monitor->m_scale;
            if (width <= frame->left + frame->right || height <= frame->top + frame->bottom || scale <= 0 || scale > 8) return;
            std::string key = std::format("{}:{}:{}:{}:{}:{}:{}:{}:{}", reinterpret_cast<uintptr_t>(frame.get()), width, height,
                                         scale, v.active, v.sticky, hover, pressed, v.title);
            if (cacheKey != key) {
                std::array<e16::Box, 4> regions = {{{0, 0, static_cast<double>(width), static_cast<double>(frame->top)},
                    {0, static_cast<double>(height - frame->bottom), static_cast<double>(width), static_cast<double>(frame->bottom)},
                    {0, static_cast<double>(frame->top), static_cast<double>(frame->left), static_cast<double>(height - frame->top - frame->bottom)},
                    {static_cast<double>(width - frame->right), static_cast<double>(frame->top), static_cast<double>(frame->right), static_cast<double>(height - frame->top - frame->bottom)}}};
                strips.clear();
                for (const auto& region : regions) {
                    if (region.w <= 0 || region.h <= 0) continue;
                    int pw = std::ceil(region.w * scale), ph = std::ceil(region.h * scale);
                    if (pw > 32768 || ph > 32768 || static_cast<int64_t>(pw) * ph > 16 * 1024 * 1024) throw std::runtime_error("Decoration texture too large");
                    auto image = e16::surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32, pw, ph));
                    auto cr = cairo_create(image.get());
                    cairo_scale(cr, scale, scale); cairo_translate(cr, -region.x, -region.y);
                    frame->paint(cr, width, height, v);
                    cairo_destroy(cr); cairo_surface_flush(image.get());
                    strips.push_back({region, g_pHyprRenderer->createTexture(image.get())});
                }
                cacheKey = key;
            }
            for (const auto& strip : strips) {
                CTexPassElement::SRenderData data;
                data.tex = strip.texture;
                data.box = CBox{box.x + strip.region.x - monitor->m_position.x, box.y + strip.region.y - monitor->m_position.y, strip.region.w, strip.region.h};
                data.box.scale(scale).round(); data.a = alpha;
                g_pHyprRenderer->addPassElement(makeUnique<CTexPassElement>(std::move(data)));
            }
        } catch (const std::exception& error) {
            // A broken theme must not throw through Hyprland's render loop.
            if (!renderFailed) {
                HyprlandAPI::addNotification(handle, std::string("E16 frame render error: ") + error.what(), CHyprColor(0xffee6666), 6000);
                renderFailed = true;
            }
        }
    }
  private:
    PHLWINDOWREF window;
    int hover = -1, pressed = -1, inputEvents = 0;
    bool wasVisible = false, renderFailed = false;
    struct Strip { e16::Box region; SP<Render::ITexture> texture; };
    std::vector<Strip> strips;
    std::string cacheKey;
};

void addWindow(PHLWINDOW window) {
    if (!window || !window->m_isMapped) return;
    for (const auto& deco : window->m_windowDecorations) if (dynamic_cast<Decoration*>(deco.get())) return;
    HyprlandAPI::addWindowDecoration(handle, window, makeUnique<Decoration>(window));
}

std::string reloadTheme(bool force = false) {
    try {
        auto path = manifestPath();
        std::string stamp = path.string();
        const bool exists = std::filesystem::exists(path);
        if (!exists && (!overridePath.empty() || !themeOption->value().empty()))
            throw std::runtime_error("Decoration manifest not found: " + path.string());
        if (exists) stamp += std::filesystem::canonical(path).string() + std::to_string(std::filesystem::last_write_time(path).time_since_epoch().count());
        if (!force && stamp == lastStamp) return "";
        lastStamp = stamp;
        auto candidate = exists ? std::make_shared<e16::Frame>(path) : nullptr;
        for (auto* decoration : decorations) decoration->damageEntire();
        frame = std::move(candidate);
        for (auto* decoration : decorations) decoration->invalidate();
        for (const auto& window : Desktop::windowState()->windows()) if (window->m_isMapped) window->updateWindowDecos();
        return "";
    } catch (const std::exception& error) {
        // Keep the last working frame when a replacement is invalid.
        return error.what();
    }
}
void reportError(const std::string& error) {
    if (!error.empty()) HyprlandAPI::addNotification(handle, "E16: " + error, CHyprColor(0xffee6666), 6000);
}
} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() { return HYPRLAND_API_VERSION; }
APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE pluginHandle) {
    handle = pluginHandle;
    if (std::string(__hyprland_api_get_hash()) != __hyprland_api_get_client_hash())
        throw std::runtime_error("E16 decorations: Hyprland ABI mismatch; rebuild against the running compositor headers");
    themeOption = makeShared<Config::Values::CStringValue>("plugin:e16:theme", "E16 decoration manifest; empty follows the current Omarchy theme", "");
    if (!HyprlandAPI::addConfigValueV2(handle, themeOption)) throw std::runtime_error("Cannot register E16 config");
    HyprlandAPI::addDispatcherV2(handle, "e16:reload", [](std::string) { auto error = reloadTheme(true); return SDispatchResult{.success = error.empty(), .error = error}; });
    HyprlandAPI::addDispatcherV2(handle, "e16:theme", [](std::string path) {
        auto old = overridePath;
        overridePath = path == "auto" ? "" : path;
        auto error = reloadTheme(true);
        if (!error.empty()) overridePath = old;
        return SDispatchResult{.success = error.empty(), .error = error};
    });
    HyprlandAPI::addLuaFunction(handle, "e16", "set_theme", [](lua_State* L) -> int {
        const char* input = luaL_checkstring(L, 1);
        auto old = overridePath;
        overridePath = std::string(input) == "auto" ? "" : input;
        auto error = reloadTheme(true);
        if (!error.empty()) overridePath = old;
        lua_pushboolean(L, error.empty());
        lua_pushlstring(L, error.data(), error.size());
        return 2;
    });
    HyprlandAPI::addLuaFunction(handle, "e16", "show_minimized", [](lua_State* L) -> int {
        auto found = g_pKeybindManager->m_dispatchers.find("togglespecialworkspace");
        if (found == g_pKeybindManager->m_dispatchers.end()) { lua_pushboolean(L, false); return 1; }
        auto result = found->second("e16-minimized");
        lua_pushboolean(L, result.success); lua_pushlstring(L, result.error.data(), result.error.size());
        return 2;
    });
    HyprlandAPI::addLuaFunction(handle, "e16", "status", [](lua_State* L) -> int {
        lua_newtable(L);
        lua_pushstring(L, frame ? frame->border.c_str() : "none"); lua_setfield(L, -2, "border");
        lua_pushinteger(L, frame ? frame->top : 0); lua_setfield(L, -2, "top");
        lua_newtable(L);
        int index = 1;
        for (auto* decoration : decorations) { decoration->pushStatus(L); lua_rawseti(L, -2, index++); }
        lua_setfield(L, -2, "windows");
        return 1;
    });
    HyprlandAPI::addLuaFunction(handle, "e16", "reload", [](lua_State* L) -> int {
        auto error = reloadTheme(true);
        lua_pushboolean(L, error.empty()); lua_pushlstring(L, error.data(), error.size());
        return 2;
    });
    listeners.push_back(Event::bus()->m_events.window.open.listen([](PHLWINDOW w) { addWindow(w); }));
    listeners.push_back(Event::bus()->m_events.window.active.listen([] { for (auto* d : decorations) d->damageEntire(); }));
    listeners.push_back(Event::bus()->m_events.window.title.listen([] { for (auto* d : decorations) d->damageEntire(); }));
    listeners.push_back(Event::bus()->m_events.window.fullscreen.listen([] { for (auto* d : decorations) d->updateWindow(nullptr); }));
    listeners.push_back(Event::bus()->m_events.config.reloaded.listen([] { reportError(reloadTheme(true)); }));
    listeners.push_back(Event::bus()->m_events.input.mouse.move.listen([](Vector2D pos, Event::SCallbackInfo&) {
        try { for (auto* d : decorations) d->mouseMove(pos); } catch (const std::exception&) {}
    }));
    listeners.push_back(Event::bus()->m_events.input.mouse.button.listen([](IPointer::SButtonEvent event, Event::SCallbackInfo& info) {
        if (event.button != 272 || event.state != WL_POINTER_BUTTON_STATE_RELEASED) return;
        try { for (auto* d : decorations) if (d->release(Pointer::mgr()->position())) { info.cancelled = true; break; } }
        catch (const std::exception& error) { reportError(error.what()); }
    }));
    themeTimer = makeShared<CEventLoopTimer>(std::chrono::milliseconds(500), [](SP<CEventLoopTimer> self, void*) {
        reportError(reloadTheme());
        self->updateTimeout(std::chrono::milliseconds(500));
    }, nullptr);
    g_pEventLoopManager->addTimer(themeTimer);
    reportError(reloadTheme(true));
    for (const auto& w : Desktop::windowState()->windows()) addWindow(w);
    return {"e16-decorations", "Original Enlightenment E16 bitmap window frames", "e16toomarchy contributors", "0.2.0"};
}
APICALL EXPORT void PLUGIN_EXIT() {
    listeners.clear();
    if (themeTimer) { themeTimer->cancel(); g_pEventLoopManager->removeTimer(themeTimer); themeTimer.reset(); }
    // PluginAPI removes registered decorations after exit. Do not leave Cairo/GPU caches owned by the module.
    std::vector<Decoration*> copy(decorations.begin(), decorations.end());
    for (auto* d : copy) { d->damageEntire(); HyprlandAPI::removeWindowDecoration(handle, d); }
    frame.reset(); themeOption.reset();
}
