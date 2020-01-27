import json
import os
import pickle
import warnings
from datetime import datetime
from functools import partial

warnings.filterwarnings('ignore')
warnings.filterwarnings(action='ignore', category=DeprecationWarning)
warnings.filterwarnings(action='ignore', category=FutureWarning)

import numpy as np
from hyperopt import hp, STATUS_OK, Trials, fmin, tpe
from hyperopt.pyll import scope
from stable_baselines import PPO2
from stable_baselines.common.policies import MlpPolicy, MlpLstmPolicy
from stable_baselines.common.vec_env import DummyVecEnv, SubprocVecEnv
from statistics import mean, stdev

from gym_locm.engine import PlayerOrder
from gym_locm.agents import MaxAttackBattleAgent, MaxAttackDraftAgent
from gym_locm.envs.draft import LOCMDraftSelfPlayEnv, LOCMDraftSingleEnv, LOCMDraftEnv

# parameters
seed = 96730
num_processes = 4

lstm = True

train_steps = 30 * 30000
eval_steps = 30 * 3000
num_evals = 10

num_trials = 50
num_warmup_trials = 20

path = 'models/hyp-search/lstm-draft-1st-player'

optimize_for = PlayerOrder.FIRST

param_dict = {
    'n_switches': hp.choice('n_switches', [10, 100, 1000, 10000]),
    'layers': hp.uniformint('layers', 1, 3),
    'neurons': hp.uniformint('neurons', 24, 128),
    'n_steps': scope.int(hp.quniform('n_steps', 30, 300, 30)),
    'nminibatches': scope.int(hp.quniform('nminibatches', 1, 300, 1)),
    'noptepochs': scope.int(hp.quniform('noptepochs', 3, 20, 1)),
    'cliprange': hp.quniform('cliprange', 0.1, 0.3, 0.1),
    'vf_coef': hp.quniform('vf_coef', 0.5, 1.0, 0.5),
    'ent_coef': hp.uniform('ent_coef', 0, 0.01),
    'learning_rate': hp.loguniform('learning_rate',
                                   np.log(0.00005),
                                   np.log(0.01))
}

if lstm:
    param_dict['n_lstm'] = hp.uniformint('n_lstm', 24, 128)
    param_dict['nminibatches'] = hp.choice('nminibatches', [num_processes])

# initializations
counter = 0
make_battle_agents = lambda: (MaxAttackBattleAgent(), MaxAttackBattleAgent())


def env_builder(seed, play_first=True, **params):
    env = LOCMDraftSelfPlayEnv2(seed=seed, battle_agents=make_battle_agents())
    env.play_first = play_first

    return lambda: env


def eval_env_builder(seed, play_first=True, **params):
    env = LOCMDraftSingleEnv(seed=seed, draft_agent=MaxAttackDraftAgent(),
                             battle_agents=make_battle_agents())
    env.play_first = play_first

    return lambda: env


def model_builder_mlp(env, **params):
    net_arch = [params['neurons']] * params['layers']

    return PPO2(MlpPolicy, env, verbose=0, gamma=1,
                policy_kwargs=dict(net_arch=net_arch),
                n_steps=params['n_steps'],
                nminibatches=params['nminibatches'],
                noptepochs=params['noptepochs'],
                cliprange=params['cliprange'],
                vf_coef=params['vf_coef'],
                ent_coef=params['ent_coef'],
                learning_rate=params['learning_rate'],
                tensorboard_log=None)


def model_builder_lstm(env, **params):
    net_arch = ['lstm'] + [params['neurons']] * params['layers']

    return PPO2(MlpLstmPolicy, env, verbose=0, gamma=1,
                policy_kwargs=dict(net_arch=net_arch, n_lstm=params['n_lstm']),
                n_steps=params['n_steps'],
                nminibatches=params['nminibatches'],
                noptepochs=params['noptepochs'],
                cliprange=params['cliprange'],
                vf_coef=params['vf_coef'],
                ent_coef=params['ent_coef'],
                learning_rate=params['learning_rate'],
                tensorboard_log=None)


model_builder = model_builder_lstm if lstm else model_builder_mlp


class LOCMDraftSelfPlayEnv2(LOCMDraftSelfPlayEnv):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.rstate = None
        self.done = [False] + [True] * (num_processes - 1)

    def create_model(self, **params):
        if lstm:
            self.rstate = np.zeros(shape=(num_processes, params['n_lstm'] * 2))

        env = [env_builder(0, **params) for _ in range(num_processes)]
        env = DummyVecEnv(env)

        self.model = model_builder(env, **params)

    if lstm:
        def step(self, action):
            """Makes an action in the game."""
            obs = self._encode_state()

            zero_completed_obs = np.zeros((num_processes, *self.observation_space.shape))
            zero_completed_obs[0, :] = obs

            prediction, self.rstate = self.model.predict(zero_completed_obs,
                                                         state=self.rstate,
                                                         mask=self.done)

            # act according to first and second players
            if self.play_first:
                LOCMDraftEnv.step(self, action)
                state, reward, self.done[0], info = \
                    LOCMDraftEnv.step(self, prediction[0])
            else:
                LOCMDraftEnv.step(self, prediction[0])
                state, reward, self.done[0], info = \
                    LOCMDraftEnv.step(self, action)
                reward = -reward

            return state, reward, self.done[0], info


def train_and_eval(params):
    global counter

    counter += 1

    # get and print start time
    start_time = str(datetime.now())
    print('Start time:', start_time)

    # ensure integer hyperparams
    params['n_steps'] = int(params['n_steps'])
    params['nminibatches'] = int(params['nminibatches'])
    params['noptepochs'] = int(params['noptepochs'])

    # ensure nminibatches <= n_steps
    params['nminibatches'] = min(params['nminibatches'],
                                 params['n_steps'])

    # ensure n_steps % nminibatches == 0
    while params['n_steps'] % params['nminibatches'] != 0:
        params['nminibatches'] -= 1

    # build the envs
    env1 = [env_builder(seed + train_steps * i, True, **params)
            for i in range(num_processes)]
    env2 = [env_builder(seed + train_steps * i, False, **params)
            for i in range(num_processes)]

    env1 = SubprocVecEnv(env1, start_method='spawn')
    env2 = SubprocVecEnv(env2, start_method='spawn')

    env1.env_method('create_model', **params)
    env2.env_method('create_model', **params)

    # build the evaluation envs
    eval_seed = seed + train_steps * num_processes

    eval_env1 = [eval_env_builder(eval_seed + eval_steps * i,  True, **params)
                 for i in range(num_processes)]
    eval_env2 = [eval_env_builder(eval_seed + eval_steps * i, False, **params)
                 for i in range(num_processes)]

    eval_env1 = SubprocVecEnv(eval_env1, start_method='spawn')
    eval_env2 = SubprocVecEnv(eval_env2, start_method='spawn')

    # build the models
    model1 = model_builder(env1, **params)
    model2 = model_builder(env2, **params)

    if optimize_for == PlayerOrder.SECOND:
        model1, model2 = model2, model1

    # update parameters on surrogate models
    env1.env_method('update_parameters', model2.get_parameters())
    env2.env_method('update_parameters', model1.get_parameters())

    # create the model name
    model_name = f'{counter}'

    # build model paths
    model_path1 = path + '/' + model_name + '/1st'
    model_path2 = path + '/' + model_name + '/2nd'

    if optimize_for == PlayerOrder.SECOND:
        model_path1, model_path2 = model_path2, model_path1

    # set tensorflow log dir
    model1.tensorboard_log = model_path1
    model2.tensorboard_log = model_path2

    # create necessary folders
    os.makedirs(model_path1, exist_ok=True)
    os.makedirs(model_path2, exist_ok=True)

    # save starting models
    model1.save(model_path1 + '/0-steps')
    model2.save(model_path2 + '/0-steps')

    results = [[[], []], [[], []]]

    model1.callback_counter = 0

    # calculate utilities
    callback_freq = model1.n_steps * num_processes
    total_callbacks = train_steps // callback_freq
    eval_every = total_callbacks // num_evals

    # ensure num of switches <= num of callbacks
    while params['n_switches'] > total_callbacks:
        params['n_switches'] /= 10

    switch_every = total_callbacks // params['n_switches']

    # print hyperparameters
    print(params)

    def make_evaluate(eval_env):
        def evaluate(model):
            """
            Evaluates a model.
            :param model: (stable_baselines model) Model to be evaluated.
            :return: The mean (win rate) and standard deviation.
            """
            # initialize structures
            episode_rewards = [[0.0] for _ in range(eval_env.num_envs)]
            num_steps = int(eval_steps / num_processes)

            # set seeds
            for i in range(num_processes):
                eval_env.env_method('seed',
                                    eval_seed + eval_steps * i,
                                    indices=[i])

            # reset the env
            obs = eval_env.reset()

            # runs `num_steps` steps
            for j in range(num_steps):
                # get a deterministic prediction from model
                actions, _ = model.predict(obs, deterministic=True)

                # do the predicted action and save the outcome
                obs, rewards, dones, _ = eval_env.step(actions)

                # save current reward into episode rewards
                for i in range(eval_env.num_envs):
                    episode_rewards[i][-1] += rewards[i]

                    if dones[i]:
                        episode_rewards[i].append(0.0)

            all_rewards = []

            # flatten episode rewards lists
            for part in episode_rewards:
                all_rewards.extend(part)

            # return the mean reward and standard deviation from all episodes
            return mean(all_rewards), stdev(all_rewards)

        return evaluate

    def callback(_locals, _globals):
        model = _locals['self']

        model.callback_counter += 1

        # if it is time to switch, do so
        if model.callback_counter % switch_every == 0:
            # train the second player model
            steps_to_train = int(model1.num_timesteps - model2.num_timesteps)

            model2.learn(total_timesteps=steps_to_train,
                         seed=seed * model1.callback_counter, tb_log_name='tf',
                         reset_num_timesteps=False)

            # update parameters on surrogate models
            env1.env_method('update_parameters', model2.get_parameters())
            env2.env_method('update_parameters', model1.get_parameters())

        # if it is time to evaluate, do so
        if model.callback_counter % eval_every == 0:
            # evaluate the models and get the metrics
            mean1, std1 = make_evaluate(eval_env1)(model)
            mean2, std2 = make_evaluate(eval_env2)(model2)

            results[0][0].append(mean1)
            results[1][0].append(mean2)
            results[0][1].append(std1)
            results[1][1].append(std2)

            # save models
            model1.save(model_path1 + f'/{model1.num_timesteps}-steps')
            model2.save(model_path2 + f'/{model2.num_timesteps}-steps')

    # train the first player model
    model1.learn(total_timesteps=train_steps,
                 callback=callback,
                 seed=seed)

    # update second player's opponent
    env2.env_method('update_parameters', model1.get_parameters())

    # train the second player model
    steps_to_train = int(model1.num_timesteps - model2.num_timesteps)

    model2.learn(total_timesteps=steps_to_train,
                 seed=seed * model1.callback_counter,
                 reset_num_timesteps=False)

    # update first player's opponent
    env1.env_method('update_parameters', model2.get_parameters())

    # evaluate the final models
    mean_reward1, std_reward1 = make_evaluate(eval_env1)(model1)
    mean_reward2, std_reward2 = make_evaluate(eval_env2)(model2)

    results[0][0].append(mean_reward1)
    results[1][0].append(mean_reward2)
    results[0][1].append(std_reward1)
    results[1][1].append(std_reward2)

    # save the final models
    model1.save(model_path1 + '/final')
    model2.save(model_path2 + '/final')

    # close the envs
    for e in (env1, env2, eval_env1, eval_env2):
        e.close()

    # get and print end time
    end_time = str(datetime.now())
    print('End time:', end_time)

    # save model info to results file
    with open(path + '/' + 'results.txt', 'a') as file:
        file.write(json.dumps(dict(id=counter, **params, results=results,
                                   start_time=start_time,
                                   end_time=end_time), indent=2))

    # calculate and return the metrics
    main_metric, aux_metric = -max(results[0][0]), -max(results[1][0])

    return {'loss': main_metric, 'loss2': aux_metric, 'status': STATUS_OK}


if __name__ == '__main__':
    try:
        with open(path + '/trials.p', 'rb') as trials_file:
            trials = pickle.load(trials_file)

        with open(path + '/rstate.p', 'rb') as random_state_file:
            random_state = pickle.load(random_state_file)

        finished_trials = len(trials.trials)
        print(f'Found run state file with {finished_trials} trials.')

        num_trials -= finished_trials
    except FileNotFoundError:
        trials = Trials()
        finished_trials = 0
        random_state = np.random.RandomState(seed)

    # noinspection PyBroadException
    try:
        algo = partial(tpe.suggest,
                       n_startup_jobs=max(0, num_warmup_trials - finished_trials))

        best_param = fmin(train_and_eval, param_dict, algo=algo,
                          max_evals=num_trials, trials=trials,
                          rstate=random_state)

        loss = [x['result']['loss'] for x in trials.trials]
        loss2 = [x['result']['loss2'] for x in trials.trials]

        print("")
        print("##### Results")
        print("Score best parameters: ", min(loss) * -1)
        print("Best parameters: ", best_param)
    finally:
        with open(path + '/trials.p', 'wb') as trials_file:
            pickle.dump(trials, trials_file)

        with open(path + '/rstate.p', 'wb') as random_state_file:
            pickle.dump(random_state, random_state_file)