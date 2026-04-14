class ScrapeError(RuntimeError):
    pass


class BlockedOrCaptchaError(ScrapeError):
    pass
