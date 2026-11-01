.PHONY: test install-menu-entry remove-menu-entry deb batocera

test:
	bash worker/tests/test.sh
	bash tests/test-orchestrator.sh
	python3 -m unittest tests/test_config_gui.py tests/test_batocera_import.py tests/test_local_setup.py tests/test_system_check.py tests/test_desktop_app.py tests/test_graphics.py tests/test_batocera.py

install-menu-entry:
	bin/winebundle --install-menu-entry

remove-menu-entry:
	bin/winebundle --remove-menu-entry

deb:
	packaging/build-deb.sh

# Copy Winebundle to a Batocera machine and add it to its Ports menu.
BATOCERA ?= root@batocera.local
batocera:
	tar -cf - --exclude=__pycache__ gui lib data VERSION packaging/batocera | \
		ssh $(BATOCERA) 'mkdir -p /userdata/system/winebundle && tar -xf - -C /userdata/system/winebundle && sh /userdata/system/winebundle/packaging/batocera/install.sh'
