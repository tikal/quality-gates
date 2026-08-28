#!/bin/bash

TEST_PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test_setup_temporary_directory() {
    TEST_TEMPORARY="$(mktemp -d)"
    trap 'rm -rf "$TEST_TEMPORARY"' EXIT
}

test_setup_repository() {
    test_setup_temporary_directory
    git -C "$TEST_TEMPORARY" init -q
    git -C "$TEST_TEMPORARY" config user.email test@example.com
    git -C "$TEST_TEMPORARY" config user.name test
}
