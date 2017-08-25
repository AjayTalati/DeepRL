#######################################################################
# Copyright (C) 2017 Shangtong Zhang(zhangshangtong.cpp@gmail.com)    #
# Permission given to modify the code as long as you keep this        #
# declaration at the top                                              #
#######################################################################

import numpy as np
import torch.multiprocessing as mp
from network import *
from utils import *
from component import *
from async_worker import *
import pickle
import os
import time

def train(id, config, learning_network, target_network):
    worker = config.worker(config, learning_network, target_network)
    episode = 0
    rewards = []
    while not config.stop_signal.value:
        steps, reward = worker.episode()
        rewards.append(reward)
        if len(rewards) > 100: rewards.pop(0)
        config.logger.debug('worker %d, episode %d, return %f, avg return %f, episode steps %d, total steps %d' % (
            id, episode, rewards[-1], np.mean(rewards[-100:]), steps, config.total_steps.value))

def evaluate(config, task, actor, critic):
    test_rewards = []
    test_points = []
    worker = config.worker(config, actor, critic)
    # config.logger = Logger('./evaluation_log', gym.logger)
    while True:
        steps = config.total_steps.value
        if steps % config.test_interval == 0:
            # worker.worker_network.load_state_dict(learning_network.state_dict())
            # with open('data/%s-%s-model-%s.bin' % (
            #         config.tag, config.worker.__name__, task.name), 'wb') as f:
            #     pickle.dump(learning_network.state_dict(), f)
            rewards = np.zeros(config.test_repetitions)
            for i in range(config.test_repetitions):
                rewards[i] = worker.episode(deterministic=True)[1]
            config.logger.info('total steps: %d, averaged return per episode: %f(%f)' % \
                               (steps, np.mean(rewards), np.std(rewards) / np.sqrt(config.test_repetitions)))
            test_rewards.append(np.mean(rewards))
            test_points.append(steps)
            with open('data/%s-%s-statistics-%s.bin' % (
                    config.tag, config.worker.__name__, task.name), 'wb') as f:
                pickle.dump([test_points, test_rewards], f)
            if np.mean(rewards) > task.success_threshold:
                config.stop_signal.value = True
                break

class AsyncAgent:
    def __init__(self, config):
        self.config = config
        self.config.steps_lock = mp.Lock()
        self.config.network_lock = mp.Lock()
        self.config.total_steps = mp.Value('i', 0)
        self.config.stop_signal = mp.Value('i', False)

    def run(self):
        config = self.config
        task = config.task_fn()
        actor = config.actor_fn()
        actor.share_memory()
        critic = config.critic_fn()
        critic.share_memory()
        # target_network = config.network_fn()
        # target_network.share_memory()
        # target_network.load_state_dict(learning_network.state_dict())

        os.environ['OMP_NUM_THREADS'] = '1'
        args = [(i, config, actor, critic) for i in range(config.num_workers)]
        args.append((config, task, actor, critic))
        procs = [mp.Process(target=train, args=args[i]) for i in range(config.num_workers)]
        procs.append(mp.Process(target=evaluate, args=args[-1]))
        for p in procs: p.start()
        while True:
            time.sleep(1)
            for i, p in enumerate(procs):
                if not p.is_alive() and not config.stop_signal.value:
                    config.logger.warning('Worker %d exited unexpectedly.' % i)
                    p.terminate()
                    if i == config.num_workers:
                        target = evaluate
                    else:
                        target = train
                    procs[i] = mp.Process(target=target, args=args[i])
                    procs[i].start()
                    self.config.logger.warning('Worker %d restarted.' % i)
                    break
            if config.stop_signal.value:
                break
        for p in procs: p.join()
