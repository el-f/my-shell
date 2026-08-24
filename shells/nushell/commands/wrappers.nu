# Wrapper helper -- resolves preferred/fallback binary at runtime.

export def _run_wrapper [preferred: string, fallback: string, error_msg: string, ...args] {
    if (which $preferred | is-not-empty) {
        run-external $preferred ...$args
    } else if (which $fallback | is-not-empty) {
        run-external $fallback ...$args
    } else {
        error make { msg: $error_msg }
    }
}
