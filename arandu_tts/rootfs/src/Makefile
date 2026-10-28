.PHONY: test sync-addon verify

test:
	python -m pytest -q

sync-addon:
	python tools/sync_addon.py

verify: test sync-addon
	python -m compileall -q arandu_tts tools
	bash -n tools/train_piper.sh
