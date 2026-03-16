import time
import os
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = r"C:\Users\USER\.gemini\antigravity\brain\79cce033-6ab7-4be0-ba99-e2a445f8b276"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

try:
    with open("test_user_id.txt") as f:
        user_id = f.read().strip()
except Exception:
    user_id = "1"

def take_screenshots():
    print("Starting playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--ignore-certificate-errors"])
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        print("Taking login selection screenshot...")
        page.goto("http://127.0.0.1:8888/login")
        time.sleep(1)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "login_selection.png"))

        print(f"Taking login PIN screenshot for user {user_id}...")
        page.goto(f"http://127.0.0.1:8888/login/{user_id}")
        time.sleep(1)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "login_pin.png"))

        print("Logging in...")
        page.fill("#pin", "1234")
        # Give JS debounce time
        time.sleep(1)
        page.click("button[type='submit']")
        time.sleep(2) 

        print("Taking dashboard screenshot...")
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "dashboard.png"))

        print("Taking leave screenshot...")
        page.goto("http://127.0.0.1:8888/leave")
        time.sleep(1)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "leave.png"))

        print("Taking past day screenshot...")
        page.goto("http://127.0.0.1:8888/time-entry/past-day")
        time.sleep(1)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "past_day.png"))

        print("Taking admin timesheet screenshot...")
        page.goto("http://127.0.0.1:8888/admin/timesheet")
        time.sleep(1)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "admin_timesheet.png"))

        print("Done!")
        browser.close()

if __name__ == '__main__':
    take_screenshots()
