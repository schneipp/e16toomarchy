.pragma library

var stages = ["idle", "queued", "starting", "catalog", "downloading", "converting", "previewing", "building", "installing", "applying", "decorating", "restoring", "ready", "applied", "restored", "failed"]

function validName(name) {
  return typeof name === "string" && /^[A-Za-z0-9][A-Za-z0-9 _+.,()'\-]{0,119}$/.test(name) && name.indexOf("..") === -1
}
function plain(value, limit) {
  return typeof value === "string" ? value.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").slice(0, limit || 1800) : ""
}
function parseCatalog(raw) {
  if (!raw || raw.length > 524288) return { themes: [], error: "Invalid catalog response" }
  try {
    var result = JSON.parse(raw)
    if (!Array.isArray(result.themes)) return { themes: [], error: plain(result.error) || "Invalid catalog response" }
    return { themes: result.themes.slice(0, 3000).filter(validName), warning: plain(result.warning, 400) }
  } catch (e) { return { themes: [], error: "Invalid catalog response" } }
}
function parseState(raw) {
  if (!raw || raw.length > 65536) return { stage: "idle", busy: false }
  try {
    var state = JSON.parse(raw)
    return {
      stage: stages.indexOf(state.stage) !== -1 ? state.stage : "idle",
      busy: state.busy === true,
      name: validName(state.name) ? state.name : "",
      mode: ["auto", "dark", "light"].indexOf(state.mode) !== -1 ? state.mode : "auto",
      kind: state.kind === "frames" ? "frames" : "full",
      preview: plain(state.preview, 4096), inactive: plain(state.inactive, 4096),
      error: plain(state.error), job: plain(state.job, 64),
      warnings: Array.isArray(state.warnings) ? state.warnings.slice(0, 20).map(function(v) { return plain(v, 400) }) : []
    }
  } catch (e) { return { stage: "failed", busy: false, error: "Invalid worker status" } }
}
function previewUrl(path, cacheRoot) {
  if (typeof path !== "string" || path.indexOf(cacheRoot + "/themes/e16-") !== 0 || path.indexOf("..") !== -1 || path.indexOf("\\") !== -1) return ""
  if (!/\/e16\/frame-(preview|inactive)\.png$/.test(path)) return ""
  return "file://" + encodeURI(path).replace(/#/g, "%23").replace(/\?/g, "%3F")
}
function filter(names, query) {
  var needle = String(query || "").toLowerCase()
  return names.filter(function(name) { return name.toLowerCase().indexOf(needle) !== -1 })
}
