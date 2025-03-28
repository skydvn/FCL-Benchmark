import copy
import torch
import torch.nn as nn
import numpy as np
import os
from torch.utils.data import DataLoader
from sklearn.preprocessing import label_binarize
from sklearn import metrics


class Client(object):
    """
    Base class for clients in federated learning.
    """

    def __init__(self, args, id, train_data, test_data, train_samples, test_samples, **kwargs):
        torch.manual_seed(0)
        self.model = copy.deepcopy(args.model)
        self.algorithm = args.algorithm
        self.dataset = args.dataset
        self.device = args.device
        self.id = id  # integer
        self.save_folder_name = args.save_folder_name

        self.num_classes = args.num_classes

        self.train_samples = train_samples
        self.test_samples = test_samples
        self.batch_size = args.batch_size
        self.learning_rate = args.local_learning_rate
        self.local_epochs = args.local_epochs

        self.train_data = train_data
        self.test_data = test_data

        self.train_source = [image for image, _ in self.train_data]
        self.train_targets = [label for _, label in self.train_data]

        self.train_loader = self.load_train_data()
        self.test_loader = self.load_test_data()

        # check BatchNorm
        self.has_BatchNorm = False
        for layer in self.model.children():
            if isinstance(layer, nn.BatchNorm2d):
                self.has_BatchNorm = True
                break

        self.train_slow = kwargs['train_slow']
        self.send_slow = kwargs['send_slow']
        self.train_time_cost = {'num_rounds': 0, 'total_cost': 0.0}
        self.send_time_cost = {'num_rounds': 0, 'total_cost': 0.0}

        self.loss = nn.CrossEntropyLoss()
        self.optimizer = torch.optim.SGD(self.model.parameters(), lr=self.learning_rate)
        self.learning_rate_scheduler = torch.optim.lr_scheduler.ExponentialLR(
            optimizer=self.optimizer,
            gamma=args.learning_rate_decay_gamma
        )
        self.learning_rate_decay = args.learning_rate_decay

        # continual federated learning
        self.test_data_so_far_loader = [DataLoader(self.test_data, 64)]
        self.test_data_per_task = [self.test_data]
        self.classes_so_far = []  # all labels of a client so far
        self.available_labels_current = []  # labels from all clients on T (current)
        self.current_labels = []  # current labels for itself
        self.classes_past_task = []  # classes_so_far (current labels excluded)
        self.available_labels_past = []  # labels from all clients on T-1
        self.available_labels = []  # l from all c from 0-T
        self.current_task = 0
        self.task_dict = {}
        self.last_copy = None
        self.if_last_copy = False
        self.args = args

    def next_task(self, train, test, label_info=None, if_label=True):

        # update last model:
        self.last_copy = copy.deepcopy(self.model)
        self.last_copy.cuda()
        self.if_last_copy = True

        # update dataset:
        self.train_data = train
        self.test_data = test

        self.train_targets = [label for _, label in self.train_data]

        self.train_loader = DataLoader(self.train_data, self.batch_size, drop_last=True,  shuffle = True)
        self.test_loader =  DataLoader(self.test_data, self.batch_size, drop_last=True)

        self.train_samples = len(self.train_data)
        self.test_samples = len(self.test_data)

        # update classes_past_task
        self.classes_past_task = copy.deepcopy(self.classes_so_far)

        # update class recorder:
        self.current_task += 1

        # update classes_so_far
        if if_label:
            self.classes_so_far.extend(label_info['labels'])
            self.task_dict[self.current_task] = label_info['labels']

            self.current_labels.clear()
            self.current_labels.extend(label_info['labels'])

        self.test_data_so_far_loader.append(DataLoader(self.test_data, 64))

        # update test data for CL: (test per task)
        self.test_data_per_task.append(self.test_data)

    def assign_task_id(self, task_dict):
        if not isinstance(task_dict, dict):
            raise ValueError("task_dict must be a dictionary")

        label_key = tuple(sorted(self.current_labels)) if isinstance(self.current_labels,
                                                                     (set, list)) else self.current_labels

        return task_dict.get(label_key, -1)  # Returns -1 if labels are not in task_dict

    def load_train_data(self, batch_size=None):
        if batch_size == None:
            batch_size = self.batch_size
        train_data = self.train_data
        return DataLoader(train_data, batch_size, drop_last=True, shuffle=True)

    def load_test_data(self, batch_size=None):
        if batch_size == None:
            batch_size = self.batch_size
        test_data = self.test_data
        return DataLoader(test_data, batch_size, drop_last=False, shuffle=True)

    def set_parameters(self, model):
        for new_param, old_param in zip(model.parameters(), self.model.parameters()):
            old_param.data = new_param.data.clone()

    def clone_model(self, model, target):
        for param, target_param in zip(model.parameters(), target.parameters()):
            target_param.data = param.data.clone()
            # target_param.grad = param.grad.clone()

    def update_parameters(self, model, new_params):
        for param, new_param in zip(model.parameters(), new_params):
            param.data = new_param.data.clone()

    def test_metrics(self):
        testloaderfull = self.load_test_data()
        # self.model = self.load_model('model')
        # self.model.to(self.device)
        self.model.eval()

        test_acc = 0
        test_num = 0
        y_prob = []
        y_true = []

        with torch.no_grad():
            for x, y in testloaderfull:
                if type(x) == type([]):
                    x[0] = x[0].to(self.device)
                else:
                    x = x.to(self.device)
                y = y.to(self.device)
                output = self.model(x)

                test_acc += (torch.sum(torch.argmax(output, dim=1) == y)).item()
                test_num += y.shape[0]

                y_prob.append(output.detach().cpu().numpy())
                nc = self.num_classes
                if self.num_classes == 2:
                    nc += 1
                lb = label_binarize(y.detach().cpu().numpy(), classes=np.arange(nc))
                if self.num_classes == 2:
                    lb = lb[:, :2]
                y_true.append(lb)

        # self.model.cpu()
        # self.save_model(self.model, 'model')

        y_prob = np.concatenate(y_prob, axis=0)
        y_true = np.concatenate(y_true, axis=0)

        auc = metrics.roc_auc_score(y_true, y_prob, average='micro')

        return test_acc, test_num, auc

    def train_metrics(self):
        trainloader = self.load_train_data()
        # self.model = self.load_model('model')
        # self.model.to(self.device)
        self.model.eval()

        train_num = 0
        losses = 0
        with torch.no_grad():
            for x, y in trainloader:
                if type(x) == type([]):
                    x[0] = x[0].to(self.device)
                else:
                    x = x.to(self.device)
                y = y.to(self.device)
                output = self.model(x)
                loss = self.loss(output, y)
                train_num += y.shape[0]
                losses += loss.item() * y.shape[0]

        # self.model.cpu()
        # self.save_model(self.model, 'model')

        return losses, train_num

    # def get_next_train_batch(self):
    #     try:
    #         # Samples a new batch for persionalizing
    #         (x, y) = next(self.iter_trainloader)
    #     except StopIteration:
    #         # restart the generator if the previous generator is exhausted.
    #         self.iter_trainloader = iter(self.trainloader)
    #         (x, y) = next(self.iter_trainloader)

    #     if type(x) == type([]):
    #         x = x[0]
    #     x = x.to(self.device)
    #     y = y.to(self.device)

    #     return x, y

    def save_item(self, item, item_name, item_path=None):
        if item_path == None:
            item_path = self.save_folder_name
        if not os.path.exists(item_path):
            os.makedirs(item_path)
        torch.save(item, os.path.join(item_path, "client_" + str(self.id) + "_" + item_name + ".pt"))

    def load_item(self, item_name, item_path=None):
        if item_path == None:
            item_path = self.save_folder_name
        return torch.load(os.path.join(item_path, "client_" + str(self.id) + "_" + item_name + ".pt"))

    # @staticmethod
    # def model_exists():
    #     return os.path.exists(os.path.join("models", "server" + ".pt"))