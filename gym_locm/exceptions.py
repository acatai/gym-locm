class GameError(Exception):
    pass


class ActionError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class FullHandError(GameError):
    pass


class EmptyDeckError(GameError):
    pass


class WardShieldError(GameError):
    pass


class NotEnoughManaError(ActionError):
    def __init__(self, message="Not enough mana."):
        super().__init__(message)


class FullLaneError(ActionError):
    def __init__(self, message="Lane is full."):
        super().__init__(message)


class MalformedActionError(ActionError):
    pass
