"""Optional real-browser layout/progressive-render check using installed Chrome.

Run with the project Python. Uses isolated, explicitly synthetic test sources in
a separate Streamlit process and writes screenshots into output/ui/.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

from websockets.sync.client import connect


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "ui"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.is_file():
        raise RuntimeError("This optional check requires Chrome at its standard Windows location")
    server_log = (OUT / "preview-server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "tests" / "fixtures" / "streamlit_preview.py"),
         "--server.headless=true", "--server.port=8508", "--server.address=127.0.0.1", "--browser.gatherUsageStats=false"],
        cwd=ROOT, stdout=server_log, stderr=server_log, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    browser = subprocess.Popen(
        [str(chrome), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--remote-debugging-port=9237", "--remote-allow-origins=http://localhost:9237",
         "--user-data-dir=" + str(OUT / ("chrome-" + uuid4().hex)), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for _ in range(50):
            try:
                pages = json.load(urlopen("http://127.0.0.1:9237/json", timeout=.5))
                page = next(p for p in pages if p["type"] == "page")
                urlopen("http://127.0.0.1:8508/_stcore/health", timeout=.5).close()
                break
            except Exception:
                time.sleep(.2)
        else:
            raise RuntimeError("Browser or preview server did not start")
        with connect(page["webSocketDebuggerUrl"], origin="http://localhost:9237", max_size=25_000_000) as ws:
            sequence = 0

            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps(dict(id=sequence, method=method, params=params or {})))
                while True:
                    response = json.loads(ws.recv(timeout=10))
                    if response.get("id") == sequence:
                        if "error" in response:
                            raise RuntimeError(response["error"])
                        return response.get("result", {})

            def js(expression):
                return call("Runtime.evaluate", dict(expression=expression, returnByValue=True))["result"].get("value")

            def wait_for(expression, attempts=100):
                for _ in range(attempts):
                    if js(expression):
                        return
                    time.sleep(.15)
                raise AssertionError("Browser condition timed out: " + expression)

            def viewport(width, height, mobile=False):
                call("Emulation.setDeviceMetricsOverride", dict(width=width, height=height, deviceScaleFactor=1, mobile=mobile))
                time.sleep(.3)

            def screenshot(name):
                metrics = js("({width: innerWidth, height: innerHeight, fullHeight: document.querySelector('[data-testid=stMain]').scrollHeight})")
                call("Emulation.setDeviceMetricsOverride", dict(width=metrics["width"], height=max(metrics["height"], metrics["fullHeight"]), deviceScaleFactor=1, mobile=metrics["width"] < 500))
                time.sleep(.15)
                data = call("Page.captureScreenshot", dict(format="png", captureBeyondViewport=True))
                (OUT / name).write_bytes(base64.b64decode(data["data"]))
                call("Emulation.setDeviceMetricsOverride", dict(width=metrics["width"], height=metrics["height"], deviceScaleFactor=1, mobile=metrics["width"] < 500))

            call("Page.enable")
            viewport(1440, 1150)
            call("Page.navigate", dict(url="http://127.0.0.1:8508"))
            wait_for("!!document.querySelector('[data-testid=stForm]')")
            time.sleep(.6)
            assert js("document.querySelector('.hero').getBoundingClientRect().bottom > document.querySelector('[data-testid=stForm]').getBoundingClientRect().top"), "Form must overlap hero"
            assert not js("document.documentElement.scrollWidth > innerWidth"), "Desktop overflow"
            assert js("document.querySelectorAll('pre code').length") == 0, "HTML displayed as a code block"
            screenshot("initial-desktop.png")
            viewport(390, 1000, True)
            assert not js("document.documentElement.scrollWidth > innerWidth"), "Mobile overflow"
            screenshot("initial-mobile.png")
            viewport(1440, 1150)
            for index, text in [(0, "Example Person"), (1, "example-test-handle")]:
                js(f"document.querySelectorAll('input[type=text]')[{index}].focus()")
                call("Input.insertText", dict(text=text))
                call("Input.dispatchKeyEvent", dict(type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9))
                call("Input.dispatchKeyEvent", dict(type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9))
            js("document.querySelector('input[type=checkbox]').click()")
            js("document.querySelector('[data-testid=stFormSubmitButton] button').click()")
            wait_for("!!document.querySelector('.profile-card')")
            assert not js("!!document.querySelector('.summary-panel')"), "Expected early profile before completion"
            assert js("document.querySelector('.progress-detail').innerText.includes('1 of 3')"), "Expected partial progress"
            screenshot("scan-progress.png")
            wait_for("!!document.querySelector('.summary-panel')")
            assert not js("document.body.innerText.includes('Searching publicly indexed pages while username checks continue')"), "Stale loading message after completion"
            assert js("document.querySelectorAll('.profile-card').length") == 2
            assert js("document.querySelectorAll('.web-card').length") == 1
            assert not js("document.documentElement.scrollWidth > innerWidth")
            screenshot("results-desktop.png")
            viewport(390, 1000, True)
            assert not js("document.documentElement.scrollWidth > innerWidth"), "Mobile result overflow"
            screenshot("results-mobile.png")
            # A second submission uses only the name: no dummy username UI should remain.
            viewport(1440, 1150)
            js("document.querySelectorAll('input[type=text]')[1].focus()")
            call("Input.dispatchKeyEvent", dict(type="keyDown", key="a", code="KeyA", windowsVirtualKeyCode=65, modifiers=2))
            call("Input.dispatchKeyEvent", dict(type="keyUp", key="a", code="KeyA", windowsVirtualKeyCode=65, modifiers=2))
            call("Input.dispatchKeyEvent", dict(type="keyDown", key="Backspace", code="Backspace", windowsVirtualKeyCode=8))
            call("Input.dispatchKeyEvent", dict(type="keyUp", key="Backspace", code="Backspace", windowsVirtualKeyCode=8))
            call("Input.dispatchKeyEvent", dict(type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9))
            call("Input.dispatchKeyEvent", dict(type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9))
            assert js("document.querySelectorAll('input[type=text]')[1].value") == ""
            js("document.querySelector('[data-testid=stFormSubmitButton] button').click()")
            wait_for("!!document.querySelector('.web-progress-card') && !Array.from(document.querySelectorAll('.summary-panel')).some(node => node.checkVisibility())")
            assert js("document.querySelector('.web-progress-card').innerText.includes('Searching the public web')"), "Missing clear name-only loading state"
            assert not js("Array.from(document.querySelectorAll('h2')).some(node => node.checkVisibility() && node.innerText === 'Username Exposure')"), "Name-only search must hide username heading"
            assert not js("Array.from(document.querySelectorAll('.metric-label')).some(node => node.checkVisibility() && node.innerText === 'Sites Checked')"), "Name-only search must hide dummy site metrics"
            wait_for("Array.from(document.querySelectorAll('.web-card')).some(node => node.checkVisibility()) && !!document.querySelector('.web-progress-card')")
            assert not js("Array.from(document.querySelectorAll('.summary-panel')).some(node => node.checkVisibility())"), "First public page should appear before all queries finish"
            assert js("document.querySelector('.query-progress-detail').innerText.includes('1 of 3')"), "Name-only search should report partial query progress"
            screenshot("name-only-progress.png")
            wait_for("Array.from(document.querySelectorAll('.summary-panel')).some(node => node.checkVisibility()) && !Array.from(document.querySelectorAll('.web-progress-card')).some(node => node.checkVisibility())")
            assert not js("Array.from(document.querySelectorAll('.web-progress-card')).some(node => node.checkVisibility())"), "Completed search must clear its loading card"
            assert js("document.querySelectorAll('.web-card').length") == 1
            assert not js("document.body.innerText.includes('Username Exposure')"), "Name-only summary should not invent a username stream"
            assert js("document.body.innerText.includes('excluded') && document.body.innerText.includes('supplied identifiers')"), "Suppressed unrelated evidence should be explained"
            assert not js("document.documentElement.scrollWidth > innerWidth")
            screenshot("name-only-results.png")
            viewport(390, 1000, True)
            assert not js("document.documentElement.scrollWidth > innerWidth"), "Name-only mobile overflow"
            screenshot("name-only-results-mobile.png")
            print("Browser checks passed: overlap, responsive cards, early matches, streamed public-web progress, and name-only results without username UI.", flush=True)
    finally:
        browser.terminate()
        server.terminate()
        server_log.close()


if __name__ == "__main__":
    main()
