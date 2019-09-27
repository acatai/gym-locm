"""
A minimal implementation of Monte Carlo tree search (MCTS) in Python 3
Luke Harold Miles, July 2019, Public Domain Dedication
See also https://en.wikipedia.org/wiki/Monte_Carlo_tree_search

Modified by Ronaldo Vieira, 2019
Original version:
https://gist.github.com/qpwo/c538c6f73727e254fdc7fab81024f6e1
"""
from collections import defaultdict
import math
import numpy as np

from gym_locm.engine import PlayerOrder


class MCTS:
    """Monte Carlo tree searcher. First rollout the tree then choose a move."""

    def __init__(self, agents, exploration_weight=1.41):
        self.agents = agents
        self.Q = defaultdict(int)  # total reward of each node
        self.N = defaultdict(int)  # total visit count for each node
        self.children = defaultdict(list)  # children of each node
        self.exploration_weight = exploration_weight

    def choose(self, node):
        """Choose the best successor of node. (Choose a move in the game)"""
        if node.is_terminal():
            raise RuntimeError(f"choose called on terminal node {node}")

        if node not in self.children:
            return node.find_random_child()

        def score(n):
            if self.N[n] == 0:
                return float("-inf")  # avoid unseen moves
            return self.Q[n] / self.N[n]  # average reward

        return max(self.children[node], key=score)

    def do_rollout(self, node):
        """"Make the tree one layer better. (Train for one iteration.)"""
        path = self._select(node)
        leaf = path[-1]

        # if not leaf.is_terminal():
        self._expand(leaf)

        reward = self._simulate(leaf)
        self._backpropagate(path, reward)

    def _select(self, node):
        """Find an unexplored descendent of `node`"""
        path = []

        while True:
            path.append(node)

            if node not in self.children or not self.children[node] \
                    or node.is_terminal():
                # node is either unexplored or terminal
                return path

            unexplored = [item for item in self.children[node]
                          if item not in self.children.keys()]

            if unexplored:
                n = unexplored.pop()
                path.append(n)
                return path
            node = self._uct_select(node)  # descend a layer deeper

    def _expand(self, node):
        """"Update the `children` dict with the children of `node`"""
        if node in self.children:
            return  # already expanded
        # self.children[node].append(node.find_random_child())
        self.children[node] = node.find_children()

    def _simulate(self, node):
        """Returns the reward for a random simulation (to completion) of `node`"""
        game = node.state.clone()

        while game.winner is None:
            action = self.agents[game.current_player.id].act(game)
            game.act(action)

        return 1 if game.winner == PlayerOrder.FIRST else -1

    def _backpropagate(self, path, reward):
        """Send the reward back up to the ancestors of the leaf"""
        for node in reversed(path):
            self.N[node] += 1

            if node.state.current_player.id == PlayerOrder.FIRST:
                self.Q[node] += reward
            else:
                self.Q[node] -= reward

    def _uct_select(self, node):
        """Select a child of node, balancing exploration & exploitation"""

        # All children of node should already be expanded:
        assert all(n in self.children for n in self.children[node])

        log_n_vertex = math.log(self.N[node])

        def uct(n):
            """Upper confidence bound for trees"""
            return self.Q[n] / self.N[n] + self.exploration_weight * math.sqrt(
                log_n_vertex / self.N[n]
            )

        return max(self.children[node], key=uct)