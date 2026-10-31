.PHONY: test install-menu-entry remove-menu-entry deb

test:
	bash worker/tests/test.sh
	bash tests/test-orchestrator.sh
	python3 -m unittest tests/test_config_gui.py tests/test_batocera_import.py tests/test_local_setup.py tests/test_system_check.py tests/test_desktop_app.py

install-menu-entry:
	bin/winebundle --install-menu-entry

remove-menu-entry:
	bin/winebundle --remove-menu-entry

deb:
	packaging/build-deb.sh
