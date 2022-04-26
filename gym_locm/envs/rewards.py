from abc import ABC, abstractmethod

from gym_locm.engine import State, PlayerOrder


class RewardFunction(ABC):
    @abstractmethod
    def calculate(self, state: State, for_player: PlayerOrder = PlayerOrder.FIRST):
        pass


class WinLossRewardFunction(RewardFunction):
    def calculate(self, state: State, for_player: PlayerOrder = PlayerOrder.FIRST):
        if state.winner == for_player:
            return 1
        elif state.winner == for_player.opposing():
            return -1
        else:
            return 0


class PlayerHealthRewardFunction(RewardFunction):
    def calculate(self, state: State, for_player: PlayerOrder = PlayerOrder.FIRST):
        return state.players[for_player].health / 30


class OpponentHealthRewardFunction(RewardFunction):
    def calculate(self, state: State, for_player: PlayerOrder = PlayerOrder.FIRST):
        return -max(0, state.players[for_player.opposing()].health) / 30


available_rewards = {
    "win-loss": WinLossRewardFunction,
    "player-health": PlayerHealthRewardFunction,
    "opponent-health": OpponentHealthRewardFunction
}


def parse_reward(reward_name: str):
    return available_rewards[reward_name.lower().replace(" ", "-")]
