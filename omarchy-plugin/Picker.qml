import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
  id: root
  moduleName: "schneipp.e16-themes"
  ipcTarget: "schneipp.e16-themes"
  manageIpc: false // The shell routes bar-widget open/close calls per monitor.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string home: Quickshell.env("HOME")
  readonly property string stateHome: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/e16toomarchy"
  readonly property string cacheHome: (Quickshell.env("XDG_CACHE_HOME") || home + "/.cache") + "/e16toomarchy"
  readonly property string dataHome: (Quickshell.env("XDG_DATA_HOME") || home + "/.local/share") + "/e16toomarchy"
  readonly property string python: dataHome + "/venv/bin/python"
  readonly property string backend: dataHome + "/backend.py"
  property var catalog: []
  property string catalogError: ""
  property string selectedTheme: ""
  property string colorMode: "auto"
  property string applyScope: "full"
  property string query: ""
  property bool inactivePreview: false
  property string previewMode: "gallery"
  property var galleryMap: ({})
  property string detailName: ""
  readonly property var galleryItem: Model.galleryEntry(galleryMap, selectedTheme)
  readonly property string currentError: launchError || (job.error && (!job.name || job.name === selectedTheme) ? job.error : "")
  readonly property bool galleryLoading: selectedTheme !== "" && galleryItem.available === undefined
  property var job: ({ stage: "idle", busy: false })
  property bool launching: false
  property string launchError: ""
  readonly property bool busy: launching || job.busy === true
  readonly property var filtered: Model.filter(catalog, query)
  readonly property bool previewMatches: selectedTheme !== "" && job.name === selectedTheme && job.mode === colorMode && job.preview !== undefined && job.preview !== ""
  readonly property string preview: previewMode === "gallery" ? Model.galleryUrl(galleryItem.preview, cacheHome)
    : previewMatches ? Model.previewUrl(inactivePreview ? job.inactive : job.preview, cacheHome) : ""
  readonly property color foreground: Commons.Color.popups.text
  readonly property color muted: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function fetchCatalog(refresh) {
    if (catalogProcess.running) return
    catalogError = ""
    catalogProcess.command = [python, backend, "catalog"].concat(refresh ? ["--refresh"] : [])
    catalogProcess.running = true
  }
  function launch(action) {
    if (busy || launcher.running) return
    launchError = ""
    launching = true
    launcher.command = [python, backend, "start", action, selectedTheme, colorMode, applyScope]
    launcher.running = true
    launchTimeout.restart()
  }
  function choose(name) {
    if (busy || !Model.validName(name)) return
    selectedTheme = name
    inactivePreview = false
    previewMode = "gallery"
    launchError = ""
    detailDelay.restart()
  }
  function acceptGallery(raw) {
    var item = Model.parseGallery(raw)
    if (!item) return
    var next = Object.assign({}, root.galleryMap)
    next[item.name] = item
    root.galleryMap = next
  }
  function fetchSelected() {
    if (!root.selectedTheme || galleryProcess.running) return
    root.detailName = root.selectedTheme
    galleryProcess.command = [python, backend, "thumbnail", root.selectedTheme]
    galleryProcess.running = true
  }
  function fetchVisible() {
    if (!root.opened || thumbnailsProcess.running || !panel.loadedContent) return
    var list = panel.loadedContent.listView
    var names = Model.visibleThemes(root.filtered, list.contentY, list.height, Commons.Style.space(58) + list.spacing)
      .filter(function(name) { return Model.galleryEntry(root.galleryMap, name).available === undefined })
    if (names.length === 0) return
    thumbnailsProcess.command = [python, backend, "thumbnails"].concat(names)
    thumbnailsProcess.running = true
  }
  function showFrame() {
    root.previewMode = "frame"
    if (!root.previewMatches && root.selectedTheme && !root.busy) root.launch("prepare")
  }
  onFilteredChanged: thumbnailsDelay.restart()
  onOpenedChanged: {
    if (opened) {
      statusFile.reload()
      recovery.running = true
      if (catalog.length === 0) fetchCatalog(false)
      thumbnailsDelay.restart()
      if (selectedTheme) detailDelay.restart()
    }
  }
  onColorModeChanged: if (selectedTheme && !busy && previewMode === "frame") previewDelay.restart()

  FileView {
    id: statusFile
    path: root.stateHome + "/status.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.job = Model.parseState(text())
      if (root.job.busy || root.job.stage === "ready" || root.job.stage === "applied" || root.job.stage === "failed") {
        root.launching = false
        launchTimeout.stop()
      }
      if (root.selectedTheme === "" && root.job.name) {
        root.selectedTheme = root.job.name
        root.colorMode = root.job.mode
        root.applyScope = root.job.kind
      }
    }
  }
  Process {
    id: galleryProcess
    stdout: StdioCollector { onStreamFinished: root.acceptGallery(text) }
    onExited: function(code) {
      if (code !== 0) root.acceptGallery(JSON.stringify({name: root.detailName, available: false}))
      if (root.selectedTheme !== root.detailName) detailDelay.restart()
    }
  }
  Process {
    id: thumbnailsProcess
    stdout: SplitParser { onRead: function(data) { root.acceptGallery(data) } }
    onExited: function(code) {
      if (code !== 0) {
        for (var i = 3; i < command.length; i++) root.acceptGallery(JSON.stringify({name: command[i], available: false}))
      }
      thumbnailsDelay.restart()
    }
  }
  Timer { id: detailDelay; interval: 120; onTriggered: root.fetchSelected() }
  Timer { id: thumbnailsDelay; interval: 150; onTriggered: root.fetchVisible() }
  Process {
    id: recovery
    command: [root.python, root.backend, "status"]
    onExited: statusFile.reload()
  }
  Process {
    id: catalogProcess
    stdout: StdioCollector {
      onStreamFinished: {
        var data = Model.parseCatalog(text)
        root.catalog = data.themes
        root.catalogError = data.error || data.warning || ""
      }
    }
    onExited: function(code) {
      if (code !== 0 && !root.catalogError) root.catalogError = strings.t("launchError")
    }
  }
  Process {
    id: launcher
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          if (result.error) { root.launchError = Model.plain(result.error); root.launching = false }
        } catch (e) { root.launchError = strings.t("launchError"); root.launching = false }
        statusFile.reload()
      }
    }
    onExited: function(code) {
      if (code !== 0) { root.launching = false; if (!root.launchError) root.launchError = strings.t("launchError") }
      statusFile.reload()
    }
  }
  Timer { id: previewDelay; interval: 180; onTriggered: if (root.selectedTheme && !root.busy) root.launch("prepare") }
  Timer {
    id: launchTimeout
    interval: 5000
    onTriggered: {
      if (root.launching) { root.launching = false; root.launchError = strings.t("launchError") }
    }
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰏘"
    tooltipText: strings.t("tooltip")
    onPressed: function(b) { if (b === Qt.LeftButton) root.toggle() }
  }

  Ui.KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: fittedContentWidth(Commons.Style.space(780))
    contentHeight: fittedContentHeight(Commons.Style.space(590))
    focusTarget: loadedContent ? loadedContent.searchField : null

    content: Item {
      id: body
      property alias searchField: search
      property alias listView: themeList
      anchors.fill: parent
      Keys.onEscapePressed: root.close()

      ColumnLayout {
        anchors.fill: parent
        spacing: Commons.Style.space(12)

        RowLayout {
          Layout.fillWidth: true
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Commons.Style.space(3)
            Text {
              textFormat: Text.PlainText
              text: strings.t("title")
              color: root.foreground
              font.family: Commons.Style.font.family
              font.pixelSize: Commons.Style.font.title
              font.bold: true
            }
            Text {
              textFormat: Text.PlainText
              text: strings.t("subtitle")
              color: root.muted
              font.family: Commons.Style.font.family
              font.pixelSize: Commons.Style.font.caption
            }
          }
          Ui.Button { text: strings.t("close"); focusable: true; onClicked: root.close() }
        }

        RowLayout {
          Layout.fillWidth: true
          Ui.TextField {
            id: search
            Layout.fillWidth: true
            placeholderText: strings.t("search")
            text: root.query
            onTextEdited: root.query = text
            Keys.onDownPressed: themeList.forceActiveFocus()
            Keys.onEscapePressed: root.close()
          }
          Ui.Button {
            iconText: "󰑓"
            tooltipText: strings.t("refresh")
            focusable: true
            enabled: !root.busy && !catalogProcess.running
            onClicked: root.fetchCatalog(true)
          }
        }

        RowLayout {
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Commons.Style.space(16)

          Rectangle {
            Layout.preferredWidth: Commons.Style.space(205)
            Layout.fillHeight: true
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
            border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
            radius: Commons.Style.cornerRadius
            clip: true

            ListView {
              id: themeList
              anchors.fill: parent
              anchors.margins: Commons.Style.space(5)
              model: root.filtered
              spacing: Commons.Style.space(2)
              boundsBehavior: Flickable.StopAtBounds
              Controls.ScrollBar.vertical: Controls.ScrollBar { }
              keyNavigationEnabled: true
              onContentYChanged: thumbnailsDelay.restart()
              onHeightChanged: thumbnailsDelay.restart()
              activeFocusOnTab: true
              Keys.onReturnPressed: if (currentIndex >= 0 && currentIndex < count) root.choose(root.filtered[currentIndex])
              Keys.onEscapePressed: search.forceActiveFocus()
              delegate: Rectangle {
                required property string modelData
                required property int index
                width: themeList.width
                height: Commons.Style.space(58)
                radius: Commons.Style.cornerRadius
                color: modelData === root.selectedTheme ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.22)
                  : hover.hovered || (themeList.activeFocus && themeList.currentIndex === index) ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08) : "transparent"
                Image {
                  id: rowThumbnail
                  anchors.left: parent.left
                  anchors.leftMargin: Commons.Style.space(5)
                  anchors.verticalCenter: parent.verticalCenter
                  width: Commons.Style.space(68)
                  height: Commons.Style.space(46)
                  source: Model.galleryUrl(Model.galleryEntry(root.galleryMap, modelData).thumbnail, root.cacheHome)
                  fillMode: Image.PreserveAspectFit
                  asynchronous: true
                  sourceSize.width: 144
                }
                Text {
                  textFormat: Text.PlainText
                  anchors.left: rowThumbnail.right
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Commons.Style.space(7)
                  anchors.rightMargin: Commons.Style.space(5)
                  text: modelData
                  color: root.foreground
                  font.family: Commons.Style.font.family
                  font.pixelSize: Commons.Style.font.bodySmall
                  elide: Text.ElideRight
                }
                HoverHandler { id: hover }
                TapHandler { enabled: !root.busy; onTapped: { themeList.currentIndex = index; root.choose(modelData) } }
              }
              Text {
                textFormat: Text.PlainText
                anchors.centerIn: parent
                width: parent.width - Commons.Style.space(12)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: catalogProcess.running ? strings.t("loading") : strings.t("empty")
                visible: themeList.count === 0
                color: root.muted
                font.family: Commons.Style.font.family
                font.pixelSize: Commons.Style.font.caption
              }
            }
          }

          ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Commons.Style.space(12)
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: root.selectedTheme || strings.t("choose")
              color: root.foreground
              font.family: Commons.Style.font.family
              font.pixelSize: Commons.Style.font.body
              font.bold: true
              wrapMode: Text.WordWrap
            }
            Rectangle {
              Layout.fillWidth: true
              Layout.fillHeight: true
              color: "#171921"
              radius: Commons.Style.cornerRadius
              clip: true
              Image {
                anchors.fill: parent
                anchors.margins: Commons.Style.space(10)
                source: root.preview
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
              }
              Text {
                textFormat: Text.PlainText
                anchors.centerIn: parent
                width: parent.width - Commons.Style.space(24)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                visible: root.preview === ""
                text: root.previewMode === "gallery" && root.selectedTheme
                  ? strings.t(root.galleryLoading ? "galleryLoading" : "galleryMissing")
                  : root.busy ? strings.t("stage." + root.job.stage) : strings.t("choose")
                color: "#cdd6f4"
                font.family: Commons.Style.font.family
                font.pixelSize: Commons.Style.font.bodySmall
              }
            }
            RowLayout {
              Layout.fillWidth: true
              Ui.Button {
                text: strings.t("gallery")
                selected: root.previewMode === "gallery"
                focusable: true
                onClicked: { root.previewMode = "gallery"; if (root.selectedTheme) detailDelay.restart() }
              }
              Ui.Button {
                text: strings.t("convertedFrame")
                selected: root.previewMode === "frame"
                enabled: !!root.selectedTheme && !root.busy
                focusable: true
                onClicked: root.showFrame()
              }
            }
            RowLayout {
              Layout.fillWidth: true
              visible: root.previewMode === "frame"
              Ui.Button { text: strings.t("active"); selected: !root.inactivePreview; focusable: true; onClicked: root.inactivePreview = false }
              Ui.Button { text: strings.t("inactive"); selected: root.inactivePreview; focusable: true; onClicked: root.inactivePreview = true }
              Item { Layout.fillWidth: true }
            }
            Ui.Dropdown {
              Layout.fillWidth: true
              showLabel: false
              value: root.colorMode
              enabled: !root.busy
              options: [{value: "auto", label: strings.t("modeAuto")}, {value: "dark", label: strings.t("modeDark")}, {value: "light", label: strings.t("modeLight")}]
              onChanged: function(value) { root.colorMode = value }
            }
            Ui.Dropdown {
              Layout.fillWidth: true
              showLabel: false
              value: root.applyScope
              enabled: !root.busy
              options: [{value: "full", label: strings.t("scopeFull")}, {value: "frames", label: strings.t("scopeFrames")}]
              onChanged: function(value) { root.applyScope = value }
            }
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Commons.Style.space(4)
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: root.currentError || root.catalogError || (root.busy && root.launching ? strings.t("stage.queued")
              : root.busy ? strings.t("stage." + root.job.stage)
              : root.previewMode === "gallery" && root.selectedTheme ? strings.t(root.galleryLoading ? "galleryLoading" : root.galleryItem.available ? "galleryReady" : "galleryMissing")
              : strings.t("stage." + root.job.stage))
            color: root.currentError ? Commons.Color.urgent : root.foreground
            wrapMode: Text.WordWrap
            maximumLineCount: 3
            elide: Text.ElideRight
            font.family: Commons.Style.font.family
            font.pixelSize: Commons.Style.font.caption
          }
          Controls.ProgressBar {
            Layout.fillWidth: true
            visible: root.busy
            indeterminate: true
            palette.highlight: Commons.Color.accent
          }
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: root.busy ? strings.t("background") : root.previewMode === "gallery" && root.galleryItem.available ? strings.t("galleryAttribution")
              : root.previewMode === "frame" && root.previewMatches && root.job.warnings && root.job.warnings.length ? strings.t("warning") : strings.t("notice")
            color: root.muted
            wrapMode: Text.WordWrap
            font.family: Commons.Style.font.family
            font.pixelSize: Commons.Style.font.caption
          }
        }

        RowLayout {
          Layout.fillWidth: true
          Text {
            textFormat: Text.PlainText
            text: strings.tn("count", root.filtered.length).arg(Commons.I18n.formatNumber(root.filtered.length))
            color: root.muted
            font.family: Commons.Style.font.family
            font.pixelSize: Commons.Style.font.caption
          }
          Item { Layout.fillWidth: true }
          Ui.Button {
            text: strings.t("restore")
            enabled: !root.busy
            focusable: true
            onClicked: root.launch("restore")
          }
          Ui.Button {
            text: strings.t(root.applyScope === "frames" ? "framesApply" : "apply")
            iconText: "󰄬"
            bordered: true
            selected: true
            enabled: !root.busy && Model.validName(root.selectedTheme)
            focusable: true
            onClicked: root.launch("apply")
          }
        }
      }
    }
  }
}
