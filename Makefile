.PHONY: test

test:
	bash worker/tests/test.sh
	bash tests/test-orchestrator.sh
	python3 -m unittest tests/test_config_gui.py tests/test_batocera_import.py
