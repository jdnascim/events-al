BASE = "/home/jnascimento/exps/2022-7set-al/7Set-AL/"

import sys
sys.path.append(BASE + "src")

from tqdm import tqdm
from utils import get_emb_vec, get_normalized_acc
from torch_geometric.data import data
from torch_geometric.loader import DataLoader
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
import networkx as nx
import numpy as np
import torch
import torch_geometric
import torch.nn.functional as F
import copy


def train_data(model, data, args):
    lr = args['lr']

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    optimizer.zero_grad()

    out = model(data)
    out = out[data.train_mask]
    res = data.y[data.train_mask].long()

    loss = F.nll_loss(out, res)
    loss.backward()
    optimizer.step()

    return loss

@torch.no_grad()
def eval_data(model, data):
    model.eval()
    logits = model(data)

    mask_train = data['train_mask']
    pred_train = logits[mask_train].max(1)[1]
    bacc_train = get_normalized_acc(data.y[mask_train], pred_train)

    mask_val = data['val_mask']
    pred_val = logits[mask_val].max(1)[1]
    acc_val = pred_val.eq(data.y[mask_val]).sum().item() / mask_val.sum().item()
    bacc_val = get_normalized_acc(data.y[mask_val], pred_val)

    mask_test = data['test_mask']
    pred_test = logits[mask_test].max(1)[1]
    bacc_test = get_normalized_acc(data.y[mask_test], pred_test)

    return bacc_train, bacc_val, bacc_test

@torch.no_grad()
def test_data(model, data):
    model.eval()
    logits = model(data)
    mask1 = data['train_mask']
    pred1 = logits[mask1].max(1)[1]
    acc1 = pred1.eq(data.y[mask1]).sum().item() / mask1.sum().item()
    mask = data['test_mask']
    pred = logits[mask].max(1)[1]
    acc = pred.eq(data.y[mask]).sum().item() / mask.sum().item()
    return acc1,acc

def train_loader(model, device, data_loader):
    optimizer = torch.optim.Adam(model.parameters(), lr=args['lr'])
    loss_fn = torch.nn.BCEWithLogitsLoss()

    model.train()
    loss = 0

    graph_dict = loader.dataset.to_dict()

    for step, batch in enumerate(tqdm(data_loader, desc="Iteration")):
        batch = batch.to(device)

        if batch.x.shape[0] == 1 or batch.batch[-1] == 0:
            pass
        else:
            ## ignore nan targets (unlabeled) when computing training loss.
            optimizer.zero_grad()
            output = model(batch).float()
            batch.y = batch.y.float()
            loss = loss_fn(output[graph_dict], batch.y[is_labeled].squeeze())

            loss.backward()
            optimizer.step()

    return loss.item()


# The evaluation function
def eval_loader(model, device, loader):
    model.eval()
    y_true = []
    y_pred = []

    for step, batch in enumerate(tqdm(loader, desc="Iterationarchitectures")):
        batch = batch.to(device)

        if batch.x.shape[0] == 1:
            pass
        else:
            with torch.no_grad():
                pred = model(batch)

            y_true.append(batch.y.view(pred.shape).detach().cpu())
            y_pred.append(pred.detach().cpu())

    y_true = torch.cat(y_true, dim = 0).numpy()
    y_pred = torch.cat(y_pred, dim = 0).numpy()

    input_dict = {"y_true": y_true, "y_pred": y_pred}

    bacc = get_normalized_acc(y_true, y_pred)
    f1 = get_f1(y_true, y_pred)

    return bacc, f1


def load_image_data(emb, graph, n_neighbours=16, hold_test=False):
    data = get_emb_vec(emb)

    out = dict()

    G = nx.Graph()

    if hold_test is False:
        emb = data["emb_mt"]
        anno = data["anno"]
        train_mask = data["train_mask"]
        val_mask = data["eval_mask"]
        test_mask = data["test_mask"]
        tweet_id = data['tweet_id']
    else:
        train_val_mask = data['train_mask'] | data['eval_mask']

        emb = data['emb_mt'][train_val_mask]
        anno = data['anno'][train_val_mask]
        train_mask = data["train_mask"][train_val_mask]
        val_mask = data["eval_mask"][train_val_mask]
        tweet_id = data['tweet_id'][train_val_mask]

    n_from = [(i, {'y':anno[i], 'x': emb[i]}) for i in range(emb.shape[0])]
    G.add_nodes_from(n_from)
    
    if graph == 'knn':
        simm = cosine_similarity(emb)
        simm[np.arange(simm.shape[0]),np.arange(simm.shape[0])] = 0
        
        for i, vec in enumerate(simm):
            partit = np.argpartition(vec, -1*n_neighbours)
            for j in range(n_neighbours):
                G.add_edge(i, partit[-1 * j])
    elif graph == 'leiden':
        tweet_id_ix = dict()
        
        for ix, id_t in enumerate(tweet_id):
            tweet_id_ix[id_t] = ix

        if hold_test is False:
            G_leiden = nx.read_gpickle(BASE + "clusterings/graphLeidenFullSum.gpickle")
        else:
            G_leiden = nx.read_gpickle(BASE + "clusterings/graphLeidenTrainSum.gpickle")

        for (u, v) in G_leiden.edges():
            G.add_edge(tweet_id_ix[u], tweet_id_ix[v])

    pyg_graph = torch_geometric.utils.from_networkx(G)

    pyg_graph.tweet_id = tweet_id
    
    if hold_test is False:
        pyg_graph.train_mask = train_mask
        pyg_graph.val_mask = val_mask
        pyg_graph.test_mask = test_mask
    else:
        pyg_graph.train_mask = train_mask
        pyg_graph.val_mask = val_mask
        pyg_graph.test_mask = np.ones_like(train_mask, np.bool_)

    return pyg_graph


def dl_image_data(knn=16, batch_size=16):
    pyg_graph = load_image_data(knn)
    loader = DataLoader(pyg_graph, batch_size=batch_size)

    return loader


def run_base(model, pyg_graph, args):
    display = args['display']

    select_best = args['select_best']
    shuffle_train_val = args['shuffle_train_val']
    seed = args['random_state']

    model.reset_parameters()

    n = pyg_graph.num_nodes
    
    train_val_mask = pyg_graph.train_mask | pyg_graph.val_mask
    y = pyg_graph.y
    
    best_model = None
    best_valid_bacc = 0
    best_test_bacc = 0
    
    for epoch in range(1, 1 + args["epochs"]):
        if shuffle_train_val is True:
            val_size = pyg_graph.val_mask.sum()
            
            train_val_ix = (train_val_mask).nonzero()[0]   
            y_train_val = pyg_graph.y[train_val_mask]

            train_ix, val_ix, _, _ = train_test_split(train_val_ix, y_train_val.cpu(), 
                                                      test_size=val_size,
                                                      stratify=y_train_val.cpu(), 
                                                      random_state=seed)

            pyg_graph.train_mask = np.zeros(n, np.bool_)
            pyg_graph.train_mask[train_ix] = True
            pyg_graph.val_mask = np.zeros(n, np.bool_)
            pyg_graph.val_mask[val_ix] = True
            
        loss = train_data(model, pyg_graph, args)
        
        if display is True:
            print('Evaluating...')
        
        train_bacc, valid_bacc, test_bacc = eval_data(model, pyg_graph)
        if valid_bacc >= best_valid_bacc and select_best == 'val':
            best_valid_bacc = valid_bacc
            best_model = copy.deepcopy(model)
        elif test_bacc >= best_test_bacc and select_best == 'test':
            best_test_bacc = test_bacc
            best_model = copy.deepcopy(model)

        if display is True:
            print(f'Epoch: {epoch:02d}, '
                f'Loss: {loss:.4f}, '
                f'Train: {100 * train_bacc:.2f}%, '
                f'Valid: {100 * valid_bacc:.2f}% '
                f'Test: {100 * test_bacc:.2f}%')
    
    return best_model, test_bacc


def validate_best_model(best_model, pyg_graph_test, args):
    display = args['display']

    train_bacc, valid_bacc, test_bacc = eval_data(best_model, pyg_graph_test)

    if display is True:
        print("---------------------")
        print("Best Model (FULL GRAPH):")
        print(f'Train: {100 * train_bacc:.2f}%, '
            f'Valid: {100 * valid_bacc:.2f}% '
            f'Test: {100 * test_bacc:.2f}%')

    return test_bacc
    
    