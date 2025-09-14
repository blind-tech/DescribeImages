# -*- coding: UTF-8 -*-
import globalPluginHandler
import gui
import gui.settingsDialogs
from gui import guiHelper
import wx
import config
import threading
import base64
import io
import addonHandler
import ui
import time
import os
import winsound
from scriptHandler import script

addonHandler.initTranslation()

SETTINGS_SECTION = "geminiImageDescriber"


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Describe Images with Gemini")

    __gestures = {
        "kb:control+shift+k": "describeScreen",
        "kb:shift+nvda+k": "chatAboutImage"
    }

    def __init__(self):
        super().__init__()
        if SETTINGS_SECTION not in config.conf:
            config.conf[SETTINGS_SECTION] = {}
        try:
            self.apiKey = config.conf[SETTINGS_SECTION].get("apiKey", "")
        except KeyError:
            self.apiKey = ""
        try:
            self.playSound = config.conf[SETTINGS_SECTION].get("playSound", True)
        except KeyError:
            self.playSound = True

        self._registerSettings()
        self.lastKeyTime = 0
        self.lastImageB64 = None
        self._stopSound = threading.Event()

    def _registerSettings(self):
        if GeminiSettingsPanel not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(GeminiSettingsPanel)

    def _playRequestSound(self):
        try:
            while not self._stopSound.is_set():
                winsound.Beep(800, 200)
                time.sleep(0.5)
        except Exception:
            pass

    @script(
        description=_("Describe screen content using Gemini"),
        gesture="kb:control+shift+k"
    )
    def script_describeScreen(self, gesture):
        if not self.apiKey:
            ui.message(_("Please enter an API key from NVDA settings first."))
            return

        now = time.time()
        doublePress = (now - self.lastKeyTime) < 0.5
        self.lastKeyTime = now

        threading.Thread(target=self._describeImage, args=(doublePress,), daemon=True).start()

    def _describeImage(self, forceWindow=False):
        try:
            if self.playSound:
                self._stopSound.clear()
                threading.Thread(target=self._playRequestSound, daemon=True).start()

            screen = wx.ScreenDC()
            size = screen.GetSize()
            bmp = wx.Bitmap(size.width, size.height)
            mem = wx.MemoryDC(bmp)
            mem.Blit(0, 0, size.width, size.height, screen, 0, 0)
            del mem

            image = bmp.ConvertToImage()
            stream = io.BytesIO()
            image.SaveFile(stream, wx.BITMAP_TYPE_PNG)
            image_data = stream.getvalue()
            self.lastImageB64 = base64.b64encode(image_data).decode("utf-8")

            import urllib.request
            import urllib.error
            import json

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={self.apiKey}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{
                    "parts": [
                        {"inline_data": {"mime_type": "image/png", "data": self.lastImageB64}},
                        {"text": "Description of the content of this image in detail (and in English)."}
                    ]
                }],
                "generation_config": {
                    "temperature": 0.4,
                    "top_k": 32,
                    "top_p": 1,
                    "max_output_tokens": 500
                }
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    description = result["candidates"][0]["content"]["parts"][0]["text"]
                    self._stopSound.set()
                    if forceWindow:
                        wx.CallAfter(ui.browseableMessage, description, _("Gemini Response"))
                    else:
                        ui.message(description)
            except urllib.error.URLError as e:
                self._stopSound.set()
                ui.message(_("Failed to connect to the internet: {error}").format(error=str(e)))
            except KeyError:
                self._stopSound.set()
                ui.message(_("Error processing server response"))
            except Exception as e:
                self._stopSound.set()
                ui.message(_("Unexpected error: {error}").format(error=str(e)))

        except Exception as e:
            self._stopSound.set()
            ui.message(_("Failed to describe image: {error}").format(error=str(e)))

    @script(
        description=_("Chat with Gemini about the last captured image"),
        gesture="kb:shift+nvda+k"
    )
    def script_chatAboutImage(self, gesture):
        if not self.apiKey:
            ui.message(_("Please enter an API key from NVDA settings first."))
            return
        if not self.lastImageB64:
            ui.message(_("No image has been captured yet."))
            return

        def openChat():
            frame = GeminiChatWindow(self.apiKey, self.lastImageB64)
            frame.Show()

        wx.CallAfter(openChat)


class GeminiChatWindow(wx.Frame):
    def __init__(self, apiKey, imageB64):
        super().__init__(None, title=_("Chat with Gemini about the image"), size=(600, 400))
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        self.history = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.input = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.sendBtn = wx.Button(panel, label=_("Send"))

        vbox.Add(self.history, 1, wx.EXPAND | wx.ALL, 5)
        vbox.Add(self.input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        vbox.Add(self.sendBtn, 0, wx.ALL | wx.ALIGN_RIGHT, 5)

        panel.SetSizer(vbox)

        self.apiKey = apiKey
        self.imageB64 = imageB64

        self.sendBtn.Bind(wx.EVT_BUTTON, self.onSend)
        self.input.Bind(wx.EVT_TEXT_ENTER, self.onSend)

    def onSend(self, event):
        userMsg = self.input.GetValue().strip()
        if not userMsg:
            return
        self.history.AppendText("You: " + userMsg + "\n")
        self.input.Clear()

        threading.Thread(target=self.askGemini, args=(userMsg,), daemon=True).start()

    def askGemini(self, message):
        import urllib.request, urllib.error, json
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={self.apiKey}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": self.imageB64}},
                    {"text": message}
                ]
            }],
            "generation_config": {
                "temperature": 0.5,
                "top_k": 32,
                "top_p": 1,
                "max_output_tokens": 400
            }
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                reply = result["candidates"][0]["content"]["parts"][0]["text"]
                wx.CallAfter(self.history.AppendText, "Gemini: " + reply + "\n")
        except Exception as e:
            wx.CallAfter(self.history.AppendText, _("❌ Error: {error}\n").format(error=str(e)))


class GeminiSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = _("Describe Images with Gemini")

    def makeSettings(self, sizer):
        sizerHelper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
        try:
            apiKeyValue = config.conf[SETTINGS_SECTION].get("apiKey", "")
        except KeyError:
            apiKeyValue = ""

        try:
            playSoundValue = config.conf[SETTINGS_SECTION].get("playSound", True)
        except KeyError:
            playSoundValue = True

        self.apiKeyCtrl = sizerHelper.addLabeledControl(
            _("Enter API key from Google AI Studio:"),
            wx.TextCtrl,
            value=apiKeyValue
        )

        self.playSoundCtrl = wx.CheckBox(self, label=_("Play waiting sound while processing"))
        self.playSoundCtrl.SetValue(playSoundValue)
        sizerHelper.addItem(self.playSoundCtrl)

        self.getKeyBtn = wx.Button(self, label=_("Get API Key from Google AI Studio"))
        sizerHelper.addItem(self.getKeyBtn)
        self.getKeyBtn.Bind(wx.EVT_BUTTON, self.onGetKey)

    def onGetKey(self, event):
        import webbrowser
        webbrowser.open("https://aistudio.google.com/app/apikey")

    def onSave(self):
        if SETTINGS_SECTION not in config.conf:
            config.conf[SETTINGS_SECTION] = {}
        config.conf[SETTINGS_SECTION]["apiKey"] = self.apiKeyCtrl.Value
        config.conf[SETTINGS_SECTION]["playSound"] = self.playSoundCtrl.GetValue()
