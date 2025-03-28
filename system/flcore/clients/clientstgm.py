import copy
import torch
import numpy as np
import time
from flcore.clients.clientbase import Client
from collections import OrderedDict


class clientSTGM(Client):
    def __init__(self, args, id, train_data, test_data, train_samples, test_samples, **kwargs):
        super().__init__(args, id, train_data, test_data, train_samples, test_samples, **kwargs)
        """ 
        - Replay memory:
            + Set maximum memory.
            + Set in/out function for memory.
        """
        self.memory_num = args.memory_num
        self.G = OrderedDict()
        self.buffer = OrderedDict()
        self.new_buffer = OrderedDict()


    def train(self):
        trainloader = self.load_train_data()
        # self.model.to(self.device)
        self.model.train()

        start_time = time.time()

        max_local_epochs = self.local_epochs
        if self.train_slow:
            max_local_epochs = np.random.randint(1, max_local_epochs // 2)

        for epoch in range(max_local_epochs):
            for i, (x, y) in enumerate(trainloader):
                if type(x) == type([]):
                    x[0] = x[0].to(self.device)
                else:
                    x = x.to(self.device)
                y = y.to(self.device)
                if self.train_slow:
                    time.sleep(0.1 * np.abs(np.random.rand()))
                output = self.model(x)
                loss = self.loss(output, y)
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

            """
            - STGM on client-side
                + Load prototype
                + Inference -> Loss -> Gradients 
                + GM on client-side
            """

        # self.model.cpu()

        if self.learning_rate_decay:
            self.learning_rate_scheduler.step()

        self.train_time_cost['num_rounds'] += 1
        self.train_time_cost['total_cost'] += time.time() - start_time