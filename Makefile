.PHONY: install test clean lint check-generated-artifacts

PATH_USER_BASE := $(patsubst %/bin,%,$(firstword $(filter %/.local/bin,$(subst :, ,$(PATH)))))
INSTALL_USER_BASE := $(if $(PATH_USER_BASE),$(PATH_USER_BASE),$(HOME)/.local)

install:
	PYTHONUSERBASE="$(INSTALL_USER_BASE)" PIP_BREAK_SYSTEM_PACKAGES=1 pip install -e .

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_gates.py -q

clean:
	rm -rf build __pycache__ .pytest_cache .ruff_cache *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

check-generated-artifacts:
	test -z "$$(git ls-files --ignored --exclude-standard -o)"

lint:
	shellcheck dispatch-gate.sh
