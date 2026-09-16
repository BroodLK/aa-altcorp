appname = aa-altcorp
package = aa_altcorp

.PHONY: help coverage pre-commit-checks tox_tests

help:
	@echo "make coverage | pre-commit-checks | tox_tests"

coverage:
	coverage run runtests.py $(package) -v 2
	coverage report -m

pre-commit-checks:
	pre-commit run --all-files

tox_tests:
	tox -v
