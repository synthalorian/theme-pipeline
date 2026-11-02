# Omarchy 4 theme release pipeline.
#   make release THEME=<theme-dir> [OUT=<dir>] [NAME=<name>]
#   make test
SHELL := /bin/bash
OUT ?= out

.PHONY: release test clean help

help:
	@echo "make release THEME=<theme-dir> [OUT=<dir>] [NAME=<name>]   validate, check, preview, package, gate"
	@echo "make test                                                   run the test suite"
	@echo "make clean                                                  remove ./out"

# THEME/OUT/NAME reach the recipe through the environment, never spliced into shell source.
export THEME OUT NAME
release:
	@if [ -z "$$THEME" ]; then echo "usage: make release THEME=<theme-dir> [OUT=<dir>] [NAME=<name>]" >&2; exit 2; fi
	@nice -n 10 ./bin/theme-release "$$THEME" --out "$$OUT" $${NAME:+--name "$$NAME"}

test:
	@./tests/run-tests.sh

clean:
	rm -rf -- out
