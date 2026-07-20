include upstream.sh

ifneq ($(strip $(ROTUNDA_RELEASE)),)
release := $(ROTUNDA_RELEASE)
endif

ifneq ($(strip $(ROTUNDA_FIREFOX_VERSION)),)
version := $(ROTUNDA_FIREFOX_VERSION)
endif

export

cf_source_dir := rotunda-$(version)-$(release)
ff_source_tarball := firefox-$(version).source.tar.xz

debs := python3 python3-dev python3-pip p7zip-full msitools wget aria2 libsqlite3-dev
rpms := python3 python3-devel p7zip msitools wget aria2 sqlite-devel
pacman := python python-pip p7zip msitools wget aria2 sqlite

.PHONY: help fetch setup setup-minimal clean set-target distclean build bundle-runtime-models package \
        revert edits run bootstrap mozbootstrap dir \
        package-linux package-macos package-windows vcredist_arch patch unpatch \
        workspace check-arg edit-cfg ff-dbg lint lint-validate lint-pythonlib lint-pythonlib-validate lint-ml lint-ml-validate tests update-ubo-assets generate-assets-car \
        generate-openapi generate-openapi-python generate-openapi-cpp generate-ml-data-models \
        validate-fingerprint-example verify-patches

DEV_UV := uv run --project . --group dev --locked
TESTS_UV := uv run --project . --package rotunda-tests --locked
PLAYWRIGHT_UV := uv run --project . --package rotunda-tests --group playwright-tests --locked
ROTUNDA_UV := uv run --project . --package rotunda --locked

BROWSERBUILD_DIR := browserbuild
BROWSERBUILD_ASSETS_DIR := $(BROWSERBUILD_DIR)/assets
BROWSERBUILD_ADDITIONS_DIR := $(BROWSERBUILD_DIR)/additions
BROWSERBUILD_PATCHES_DIR := $(BROWSERBUILD_DIR)/patches
BROWSERBUILD_SETTINGS_DIR := $(BROWSERBUILD_DIR)/settings
BROWSERBUILD_BUNDLE_DIR := $(BROWSERBUILD_DIR)/bundle

OPENAPI_SCHEMA := schemas/rotunda-profile.openapi.yaml
PY_OPENAPI_MODELS := pythonlib/rotunda/_generated_profile.py
ML_DATA_OPENAPI_SCHEMA := schemas/rotunda-ml-data-capture.openapi.yaml
PY_ML_DATA_MODELS := ml-models/rotunda_models/_generated_data_capture.py
CPP_OPENAPI_OUT := $(BROWSERBUILD_ADDITIONS_DIR)/rotundacfg/generated/profile
CPP_OPENAPI_TEMPLATES := schemas/openapi-templates/cpp-nlohmann
OPENAPI_GENERATOR_IMAGE ?= openapitools/openapi-generator-cli:v7.22.0
OPENAPI_GENERATOR ?= docker run --rm -v $(CURDIR):/local $(OPENAPI_GENERATOR_IMAGE)
OPENAPI_SCHEMA_ARG ?= /local/$(OPENAPI_SCHEMA)
CPP_OPENAPI_OUT_ARG ?= /local/$(CPP_OPENAPI_OUT)

help:
	@echo "Available targets:"
	@echo "  fetch           - Fetch the Firefox source code"
	@echo "  setup           - Setup Rotunda & local git repo for development"
	@echo "  bootstrap       - Set up build environment"
	@echo "  mozbootstrap    - Sets up mach"
	@echo "  dir             - Prepare Rotunda source directory with BUILD_TARGET"
	@echo "  revert          - Kill all working changes"
	@echo "  edits           - Rotunda developer UI"
	@echo "  clean           - Remove build artifacts"
	@echo "  distclean       - Remove everything including downloads"
	@echo "  build           - Build Rotunda"
	@echo "  bundle-runtime-models - Copy runtime models into local dev dist builds"
	@echo "  set-target      - Change the build target with BUILD_TARGET"
	@echo "  package-linux   - Package Rotunda for Linux"
	@echo "  package-macos   - Package Rotunda for macOS"
	@echo "  package-windows - Package Rotunda for Windows"
	@echo "  run             - Run Rotunda"
	@echo "  lint            - Run Python lint fixes and static analysis"
	@echo "  lint-validate   - Run Python static analysis without modifying files"
	@echo "  edit-cfg        - Edit rotunda.cfg"
	@echo "  ff-dbg          - Setup vanilla Firefox with minimal patches"
	@echo "  patch           - Apply a patch"
	@echo "  unpatch         - Remove a patch"
	@echo "  workspace       - Sets the workspace to a patch, assuming its applied"
	@echo "  tests           - Runs the Python integration test suites"
	@echo "  update-ubo-assets - Update the uBOAssets.json file"
	@echo "  generate-openapi - Generate Python and C++ profile models from OpenAPI schema"
	@echo "  generate-ml-data-models - Generate ML data capture Pydantic models from OpenAPI schema"
	@echo "  validate-fingerprint-example - Validate example/fingerprint.json against the OpenAPI schema"
	@echo "  verify-patches  - Fast Firefox patch verification against the matching source tarball"

_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(eval $(_ARGS):;@:)

fetch:
	# Fetching the Firefox source tarball...
	aria2c -x16 -s16 -k1M -o $(ff_source_tarball) "https://archive.mozilla.org/pub/firefox/releases/$(version)/source/firefox-$(version).source.tar.xz"; \

setup-minimal:
	# Note: Only docker containers are intended to run this directly.
	# Run this before `make dir` or `make build` to avoid setting up a local git repo.
	if [ ! -f $(ff_source_tarball) ]; then \
		make fetch; \
	fi
	# Create new cf_source_dir
	rm -rf $(cf_source_dir)
	mkdir -p $(cf_source_dir)
	tar -xJf $(ff_source_tarball) -C $(cf_source_dir) --strip-components=1
	# Copy browser build settings and additions
	cd $(cf_source_dir) && bash ../scripts/copy-additions.sh $(version) $(release)

setup: setup-minimal
	# Initialize local git repo for development
	cd $(cf_source_dir) && \
		git init -b main && \
		git add -f -A && \
		git commit -m "Initial commit" && \
		git tag -a unpatched -m "Initial commit"

ff-dbg: setup
	# Only apply patches to help debug vanilla Firefox
	make patch $(BROWSERBUILD_PATCHES_DIR)/chromeutil.patch
	make patch $(BROWSERBUILD_PATCHES_DIR)/browser-init.patch
	echo "LOCAL_INCLUDES += ['/rotundacfg']" >> $(cf_source_dir)/dom/base/moz.build
	touch $(cf_source_dir)/_READY
	make checkpoint
	make build

revert:
	cd $(cf_source_dir) && git reset --hard unpatched

dir:
	@if [ ! -d $(cf_source_dir) ]; then \
		make setup; \
	fi
	python3 scripts/patch.py $(version) $(release)
	touch $(cf_source_dir)/_READY

set-target:
	python3 scripts/patch.py $(version) $(release) --mozconfig-only

mozbootstrap:
	cd $(cf_source_dir) && MOZBUILD_STATE_PATH=$$HOME/.mozbuild ./mach --no-interactive bootstrap --application-choice=browser

bootstrap: dir
	(sudo apt-get -y install $(debs) || sudo dnf -y install $(rpms) || sudo pacman -Sy $(pacman))
	make mozbootstrap

diff:
	@cd $(cf_source_dir) && git diff first-checkpoint $(_ARGS)

first-checkpoint:
	cd $(cf_source_dir) && \
		git tag -d first-checkpoint || true && \
		git add -A && \
		git reset -q _READY || true && \
		git commit -m "Checkpoint" -uno && \
		git tag -a first-checkpoint -m "Checkpoint"

checkpoint:
	cd $(cf_source_dir) && git commit -m "Checkpoint" -uno

clean:
	cd $(cf_source_dir) && git clean -fdx && ./mach clobber
	make revert

distclean:
	rm -rf $(cf_source_dir) $(ff_source_tarball)

build: unbusy
	@if [ ! -f $(cf_source_dir)/_READY ]; then \
		make dir; \
	fi
	cd $(cf_source_dir) && ./mach build $(_ARGS)
	$(MAKE) bundle-runtime-models

bundle-runtime-models:
	@set -e; \
	copied=0; \
	for resources in $(cf_source_dir)/obj-*/dist/Rotunda.app/Contents/Resources; do \
		if [ -d "$$resources" ]; then \
			mkdir -p "$$resources/runtime-models"; \
			cp -R $(BROWSERBUILD_BUNDLE_DIR)/runtime-models/. "$$resources/runtime-models/"; \
			echo "Bundled runtime models -> $$resources/runtime-models"; \
			copied=1; \
		fi; \
	done; \
	for bin in $(cf_source_dir)/obj-*/dist/bin; do \
		if [ -d "$$bin" ]; then \
			mkdir -p "$$bin/runtime-models"; \
			cp -R $(BROWSERBUILD_BUNDLE_DIR)/runtime-models/. "$$bin/runtime-models/"; \
			echo "Bundled runtime models -> $$bin/runtime-models"; \
			copied=1; \
		fi; \
	done; \
	if [ "$$copied" -eq 0 ]; then \
		echo "No local dev dist found for runtime models."; \
	fi

edits:
	python3 ./scripts/developer.py $(version) $(release)

package-linux:
	uv run scripts/package.py linux \
		--include $(BROWSERBUILD_SETTINGS_DIR)/chrome.css \
		--include $(BROWSERBUILD_BUNDLE_DIR)/runtime-models \
		--include $(BROWSERBUILD_BUNDLE_DIR)/fontconfig \
		--version $(version) \
		--release $(release) \
		--arch $(arch) \
		--font linux

package-macos:
	uv run scripts/package.py macos \
		--include $(BROWSERBUILD_SETTINGS_DIR)/chrome.css \
		--include $(BROWSERBUILD_BUNDLE_DIR)/runtime-models \
		--version $(version) \
		--release $(release) \
		--arch $(arch) \
		--font macos

package-windows:
	uv run scripts/package.py windows \
		--include $(BROWSERBUILD_SETTINGS_DIR)/chrome.css \
		--include $(BROWSERBUILD_BUNDLE_DIR)/runtime-models \
		--include ~/.mozbuild/vs/VC/Redist/MSVC/14.38.33135/$(vcredist_arch)/Microsoft.VC143.CRT/*.dll \
		--version $(version) \
		--release $(release) \
		--arch $(arch) \
		--font windows

run:
	cd $(cf_source_dir) \
	&& rm -rf ~/.rotunda obj-x86_64-pc-linux-gnu/tmp/profile-default \
	&& printf '{"debug":true}\n' > /tmp/rotunda-debug-profile.json \
	&& ROTUNDA_CONFIG_PATH=/tmp/rotunda-debug-profile.json ./mach run $(args)

edit-cfg:
	@if [ ! -f $(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda.cfg ]; then \
		echo "Error: rotunda.cfg not found. Apply config.patch first."; \
		exit 1; \
	fi
	$(EDITOR) $(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda.cfg

check-arg:
	@if [ -z "$(_ARGS)" ]; then \
		echo "Error: No file specified. Usage: make <command> $(BROWSERBUILD_PATCHES_DIR)/file.patch"; \
		exit 1; \
	fi

grep:
	grep "$(_ARGS)" -r $(BROWSERBUILD_PATCHES_DIR)/*.patch

patch:
	@make check-arg $(_ARGS);
	cd $(cf_source_dir) && patch -p1 -i ../$(_ARGS)

unpatch:
	@make check-arg $(_ARGS);
	cd $(cf_source_dir) && patch -p1 -R -i ../$(_ARGS)

workspace:
	@make check-arg $(_ARGS);
	@if (cd $(cf_source_dir) && patch -p1 -R --dry-run --force -i ../$(_ARGS)) > /dev/null 2>&1; then \
		echo "Patch is already applied. Unapplying..."; \
		make unpatch $(_ARGS); \
	else \
		echo "Patch is not applied. Proceeding with application..."; \
	fi
	make first-checkpoint || true
	make patch $(_ARGS)

lint: lint-pythonlib lint-ml

lint-validate: lint-pythonlib-validate lint-ml-validate

lint-pythonlib:
	$(DEV_UV) ruff check --fix --config pythonlib/pyproject.toml pythonlib/rotunda
	$(DEV_UV) ty check pythonlib/rotunda

lint-pythonlib-validate:
	$(DEV_UV) ruff check --config pythonlib/pyproject.toml pythonlib/rotunda
	$(DEV_UV) ty check pythonlib/rotunda

lint-ml:
	$(DEV_UV) ruff check --fix --config ml-models/pyproject.toml ml-models/rotunda_models __tests__/ml_models

lint-ml-validate:
	$(DEV_UV) ruff check --config ml-models/pyproject.toml ml-models/rotunda_models __tests__/ml_models

tests:
	ROTUNDA_EXECUTABLE_PATH=$(CURDIR)/$(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda-bin \
		$(PLAYWRIGHT_UV) pytest \
			--integration \
			-vv \
			$(if $(filter true,$(headful)),, --headless) \
			__tests__/ml_models/ \
			__tests__/build-tester/ \
			__tests__/playwright/async/ \
			__tests__/service-tester/

unbusy:
	rm -rf $(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda-bin \
		$(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda

path:
	@realpath $(cf_source_dir)/obj-x86_64-pc-linux-gnu/dist/bin/rotunda-bin

update-ubo-assets:
	bash ./scripts/update-ubo-assets.sh

generate-assets-car:
	bash ./scripts/generate-assets-car.sh

generate-openapi: generate-openapi-python generate-openapi-cpp

generate-openapi-python:
	uvx --from datamodel-code-generator datamodel-codegen \
		--input $(OPENAPI_SCHEMA) \
		--input-file-type openapi \
		--output $(PY_OPENAPI_MODELS) \
		--output-model-type pydantic_v2.BaseModel \
		--target-python-version 3.10 \
		--use-standard-collections \
		--use-union-operator \
		--field-constraints \
		--snake-case-field \
		--extra-fields forbid \
		--disable-timestamp

generate-ml-data-models:
	ML_DATA_OPENAPI_SCHEMA=$(ML_DATA_OPENAPI_SCHEMA) \
	PY_ML_DATA_MODELS=$(PY_ML_DATA_MODELS) \
	bash ./scripts/generate-ml-data-models.sh

generate-openapi-cpp:
	rm -rf $(CPP_OPENAPI_OUT)
	$(OPENAPI_GENERATOR) generate \
		-i $(OPENAPI_SCHEMA_ARG) \
		-g cpp-tiny \
		-o $(CPP_OPENAPI_OUT_ARG) \
		-t /local/$(CPP_OPENAPI_TEMPLATES) \
		--global-property models,modelTests=false \
		--type-mappings number=double \
		--additional-properties hideGenerationTimestamp=true,modelPackage=rotundacfg

validate-fingerprint-example:
	uv run scripts/validate_fingerprint_example.py

verify-patches:
	uv run scripts/verify_firefox_patches.py

vcredist_arch := $(shell echo $(arch) | sed 's/x86_64/x64/')
