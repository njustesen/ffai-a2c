import torch
from multiprocessing import Process, Pipe
import numpy as np
import ffai.ai
from ffai.ai.env import FFAIEnv
import math
import uuid
import tkinter as tk
from ffai.core.game import *
from ffai.core.load import *
from ffai.ai.bots import RandomBot
from ffai.ai.layers import *
import matplotlib.pyplot as plt


def worker(remote, parent_remote, env):
    global player_id
    parent_remote.close()

    total_reward = 0.0
    episode_reward = 0.0
    episode_cnt = 0.0
    step_cnt = 0

    episode_touchdown_cnt = 0
    episode_interception_cnt = 0
    episode_pass_cnt = 0
    episode_casualty_cnt = 0

    total_touchdown_cnt = 0
    total_interception_cnt = 0
    total_pass_cnt = 0
    total_casualty_cnt = 0


    last_touchdowns = 0
    last_opp_casualties = 0
    vars = []
    episode_events = np.zeros(5)  # Number of events 5

    def get_bb_vars(obs):
        ball_layer = obs['board']['balls']
        own_player_layer = obs['board']['own players']

        item_index = np.where(ball_layer == 1)
        team_has_ball = own_player_layer[item_index]  # Gets index value of the index where the ball is

        vars = [obs['state']['own score'],  # 0. Change in score
                # np.count_nonzero(obs['board']['own players'] == 1),  # 1. Own number of players on pitch
                # np.count_nonzero(obs['board']['standing players'] == 1),  # 2. Standing players
                team_has_ball,  # 1. Team has ball
                # obs['state']['own reroll available'],  # 4. Number of re-rolls
                # obs['board']['own players'],  # 5. Own-player layer (movement-event)
                # obs['state']['own turns'],  # 6. Change in own number of turns
                obs['state']['is blitz'],  # 2. Change in if current turn is blitz
                obs['state']['is move action'],  # 3. Is move action
                obs['state']['is blitz action']  # 4. is blitz action
                ]

        return vars

    def get_events(vars, last_vars):
        num_events = 5
        events = np.zeros(num_events)

        # Change in score
        if vars[0] > last_vars[0]:
            events[0] = 1

        # # Change in own number of players
        # if vars[1] > last_vars[1]:
        #     events[1] = 1
        #
        # # Change in standing players
        # if vars[2] > last_vars[2]:
        #     events[2] = 1

        # Change in if team has acquired ball or still has ball
        # if vars[3] > last_vars[3]:
        if vars[1] == 1:
            events[1] = 1

        # # Change in number of re-rolls
        # # It's good to have a high number of re-rolls
        # if vars[4] > last_vars[4]:
        #     events[4] = 1

        # # Change in player layer (MOVEMENT)
        # own_players_curr = vars[5]
        # own_players_last = last_vars[5]
        # if not np.array_equal(own_players_curr, own_players_last):
        #     events[5] = 1
        #
        # # Change in own number of turns
        # # If number of turns is the same as before (i.e. reward it for having the turn)
        # if vars[6] == last_vars[6]:
        #     events[6] = 1

        # Change in if current turn is blitz (Assuming blitzing is good)
        if vars[2] > last_vars[2]:
            events[2] = 1

        # Change in if selects move action, if not selected before
        if vars[3] > last_vars[3]:
            events[3] = 1

        # Change in if selects blitz actions, if not selected before
        if vars[4] > last_vars[4]:
            events[4] = 1
        return events

    def check_team_has_ball(obs):
        ball_layer = obs['board']['balls']
        own_player_layer = obs['board']['own players']

        item_index = np.where(ball_layer == 1)
        if own_player_layer[item_index] == 1:
            team_has_ball = True
        else:
            team_has_ball = False
        return team_has_ball

    def get_stats():
        return episode_cnt, total_reward, total_touchdown_cnt, total_interception_cnt, total_pass_cnt, \
               total_casualty_cnt

    while True:
        command, data = remote.recv()

        if command == 'step':
            action = data
            if len(vars) == 0:
                vars = get_bb_vars(env.last_obs)
            last_vars = vars
            events = []
            try:
                obs, reward, done, info = env.step(action)
            except:
                obs = env.reset()
                last_touchdowns = 0
                last_opp_casualties = 0
                reward = 0
                done = True
                info = None
                events = [0]*5
                remote.send((obs, reward, done, info, events))

            # INTERCEPTION
            if action['action-type'] == 20:
                outcomes = env.game.state.reports[-5:] if len(env.game.state.reports) >= 5 else env.game.state.reports
                for outcome in reversed(outcomes):
                    if outcome.outcome_type.value == 46:
                        reward += 2
                        print("Interception!")
                        episode_interception_cnt += 1

            # PASS
            if action['action-type'] == 24:
                outcomes = env.game.state.reports[-5:] if len(env.game.state.reports) >= 5 else env.game.state.reports
                for outcome in reversed(outcomes):
                    if outcome.outcome_type.value == 108:
                        print("pass catched")
                        reward += 1
                        episode_pass_cnt += 1
                        break

            # TOUCHDOWN
            if info is not None:
                if info['touchdowns'] > last_touchdowns:
                    reward += 3
                    print("TOUCHDOWN!")
                    episode_touchdown_cnt += 1
                last_touchdowns = info['touchdowns'] if info['touchdowns'] is not None else 0  # always update number of touchdowns to compare next step

                # CASUALTIES
                if info['opp_cas_inflicted'] > last_opp_casualties:
                    outcomes = env.game.state.reports[-20:] if len(env.game.state.reports) >= 5 else env.game.state.reports
                    player_id = None
                    for outcome in reversed(outcomes):
                        if outcome.outcome_type.value == 73:  # If casualty  (If val in [73, 39, 40, 42, 43, 44, 79]:  # CASUALTY ENUM NUMBER)
                            player_id = outcome.player.player_id
                        if outcome.outcome_type.value == 119:
                            if player_id == outcome.player.player_id:
                                reward += 2
                                print("Casualty inflicted by block!")
                                episode_casualty_cnt += 1
                                break
                last_opp_casualties = info['opp_cas_inflicted'] if info['opp_cas_inflicted'] is not None else 0  # same as with touchdowns

            vars = get_bb_vars(obs)
            events = get_events(vars, last_vars)
            step_cnt += 1
            episode_reward += reward

            if done:
                obs = env.reset()
                last_touchdowns = 0
                last_opp_casualties = 0

                total_reward += episode_reward
                total_touchdown_cnt += episode_touchdown_cnt
                total_interception_cnt += episode_interception_cnt
                total_pass_cnt += episode_pass_cnt
                total_casualty_cnt += episode_casualty_cnt

                episode_touchdown_cnt = 0
                episode_interception_cnt = 0
                episode_pass_cnt = 0
                episode_casualty_cnt = 0
                episode_reward = 0
                episode_cnt += 1

                vars = get_bb_vars(obs)

            remote.send((obs, reward, done, info, events))

        elif command == 'reset':
            obs = env.reset()
            remote.send(obs)

        elif command == 'actions':
            mask = torch.zeros(37)
            available_actions = env.available_action_types()
            for action in available_actions:
                mask[action] = 1
            remote.send(mask)

        elif command == 'positions':
            action = data
            action = action.data.squeeze(0).numpy()  # In order for env to handle it
            mask = torch.zeros(7*14+1)
            available_positions = env.available_positions(action)
            if len(available_positions) == 0:
                mask[-1] = 1
            else:
                for pos in available_positions:
                    new_pos = pos.x + 14 * pos.y
                    mask[new_pos] = 1
            remote.send(mask)

        elif command == 'render':
            env.render()

        elif command == "log":
            epi_cnt, tot_reward, tot_touchdown_cnt, tot_interception_cnt, tot_pass_cnt, \
                tot_casualty_cnt = get_stats()

            episode_cnt = 0
            total_reward = 0
            total_touchdown_cnt = 0
            total_interception_cnt = 0
            total_pass_cnt = 0
            total_casualty_cnt = 0

            remote.send((epi_cnt, tot_reward, tot_touchdown_cnt, tot_interception_cnt, tot_pass_cnt,
                        tot_casualty_cnt))


class VecEnv():
    def __init__(self, envs):
        """
        envs: list of blood bowl game environments to run in subprocesses
        """
        self.closed = False
        nenvs = len(envs)
        self.remotes, self.work_remotes = zip(*[Pipe() for _ in range(nenvs)])

        self.ps = [Process(target=worker, args=(work_remote, remote, env))
            for (work_remote, remote, env) in zip(self.work_remotes, self.remotes, envs)]

        for p in self.ps:
            p.daemon = True  # If the main process crashes, we should not cause things to hang
            p.start()
        for remote in self.work_remotes:
            remote.close()

    def step(self, actions):
        cumul_rewards = None
        cumul_dones = None
        cumul_events = None

        for remote, action in zip(self.remotes, actions):
            remote.send(('step', action))
        results = [remote.recv() for remote in self.remotes]

        obs, rews, dones, infos, events = zip(*results)
        if cumul_rewards is None:
            cumul_rewards = np.stack(rews)
        else:
            cumul_rewards += np.stack(rews)
        if cumul_dones is None:
            cumul_dones = np.stack(dones)
        else:
            cumul_dones |= np.stack(dones)
        if cumul_events is None:
            cumul_events = events
        else:
            cumul_events = np.add(cumul_events, events)
        return np.stack(obs), cumul_rewards, cumul_dones, infos, cumul_events

    def actions(self):
        for remote in self.remotes:
            remote.send(('actions', None))
        return torch.stack(([remote.recv() for remote in self.remotes]))

    def positions(self, actions):
        for remote, action in zip(self.remotes, actions):
            remote.send(('positions', action))
        return torch.stack([remote.recv() for remote in self.remotes])

    def reset(self):
        for remote in self.remotes:
            remote.send(('reset', None))
        return np.stack([remote.recv() for remote in self.remotes])

    def log(self):
        for remote in self.remotes:
            remote.send(('log', None))
        results = [remote.recv() for remote in self.remotes]
        episodes, rewards, touchdowns, interceptions, passes, casualties = zip(*results)
        return np.stack(episodes), np.stack(rewards), np.stack(touchdowns), np.stack(interceptions), np.stack(passes), np.stack(casualties)

    def render(self):
        for remote in self.remotes:
            remote.send(('render', None))
        return

    def close(self):
        if self.closed:
            return

        for remote in self.remotes:
            remote.send(('close', None))
        for p in self.ps:
            p.join()
        self.closed = True

    @property
    def num_envs(self):
        return len(self.remotes)
