from pathlib import Path
text = Path("bot/mvp_bot.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(text, start=1):
    if 580 <= idx <= 640:
        print(f"{idx}: {line}")
