import torch
import pandas as pd
import numpy as np
from torch.special import expit as sigmoid
from torch.optim import Adam
import time
from datetime import datetime
from aif360.datasets import CompasDataset, BankDataset
from sklearn.model_selection import train_test_split
import argparse


parser = argparse.ArgumentParser(description='Locally separable run')
parser.add_argument('flatval', type=float)
parser.add_argument('--dummy', action='store_true')
parser.add_argument('--cuda', action='store_true')
args = parser.parse_args()
flatval = args.flatval
dummy = args.dummy
useCUDA = args.cuda

# Enable GPU if desired. Sometimes returns false values
if useCUDA:
    torch.cuda.set_device('cuda:0')
else:
    torch.device('cuda:0')


def loss_fn_generator(x_0: torch.Tensor, y: torch.Tensor, feature_num: int, sensitives: torch.Tensor,
                      alpha: list, minimize: bool):
    """
    Factory for the loss function that pytorch runs will be optimizing in WLS
    :param x_0: the data tensor with intercept column
    :param y: the target tensor
    :param initial_val: expressivity over full dataset
    :param feature_num: Which feature in the data do we care about
    :param sensitives: tensor representing sensitive features
    :param alpha: desired subgroup size
    :param minimize: Boolean -- are we minimizing or maximizing
    :return: a loss function for our particular WLS problem.
    """
    x = remove_intercept_column(x_0)

    basis_list = [[0. for _ in range(x.shape[1])]]
    basis_list[0][feature_num] = 1.
    flat_list = [flatval for _ in range(x.shape[1])]

    if useCUDA:
        basis = torch.tensor(basis_list, requires_grad=True).cuda()
        flat = torch.tensor(flat_list, requires_grad=True).cuda()
    else:
        basis = torch.tensor(basis_list, requires_grad=True)
        flat = torch.tensor(flat_list, requires_grad=True)

    if minimize:
        sign = 1
    else:
        sign = -1

    # Look into derivation of gradient by hand and implementing it here instead.
    def loss_fn(params):
        # only train using sensitive features
        one_d = sigmoid(x_0 @ (sensitives * params))
        diag = torch.diag(one_d)
        x_t = torch.t(x)
        denom = torch.inverse((x_t @ diag @ x) + torch.diag(flat))
        coefficient = basis @ (denom @ (x_t @ diag @ y))

        size = torch.sum(one_d)/x.shape[0]
        size_penalty = 100000*(max(alpha[0]-size, 0) + max(size-alpha[1], 0))

        return size_penalty + .1 * sign * coefficient

    return loss_fn


def train_and_return(x: torch.Tensor, y: torch.Tensor, feature_num: int, f_sensitive: list, alpha: list, minimize: bool):
    """
    Given an x, y, feature num, and the expressivity over the whole dataset,
    returns the differential expressivity and maximal subset for that feature
    :param x: The data tensor
    :param y: the target tensor
    :param feature_num: which feature to optimize, int.
    :param initial_val: What the expressivity over the whole dataset for the feature is.
    :param f_sensitive: indices of sensitive features
    :param alpha: target subgroup size
    :return: the differential expressivity and maximal subset weights.
    """
    niters = 1000
    # Set seed to const value for reproducibility
    torch.manual_seed(seed)
    s_list = [0. for _ in range(x.shape[1])]
    for f in f_sensitive:
        s_list[f] = 1.
    if useCUDA:
        sensitives = torch.tensor(s_list, requires_grad=True).cuda()
        params_max = torch.randn(x.shape[1], requires_grad=True, device="cuda")
    else:
        sensitives = torch.tensor(s_list, requires_grad=True)
        params_max = torch.randn(x.shape[1], requires_grad=True)

    optim = Adam(params=[params_max], lr=0.05)
    iters = 0
    curr_error = 10000
    s_record = []
    p_record = []
    loss_max = loss_fn_generator(x, y, feature_num, sensitives, alpha, minimize)
    while iters < niters:
        optim.zero_grad()
        loss_res = loss_max(params_max)
        loss_res.backward()
        optim.step()
        curr_error = loss_res.item()

        params_temp = sensitives * params_max
        size = np.sum((sigmoid(x @ params_temp)).cpu().detach().numpy())/x.shape[0]
        s_record.append(size)

        p_record.append(final_value(x, y, params_temp.cpu().detach().numpy(), feature_num)[0])
        iters += 1
    params_max = sensitives * params_max
    max_error = curr_error * -1
    assigns = (sigmoid(x @ params_max)).cpu().detach().numpy()
    #print('final train size: ',torch.sum(sigmoid(x @ params_max))/x.shape[0])
    return max_error, assigns, params_max.cpu().detach().numpy(), s_record, p_record

def is_valid(assigns, alpha):
    """
    return: 1 if valid, 0 if invalid
    """
    size = np.mean(assigns)
    if size-alpha[0]>0 and alpha[1]-size>0:
        valid = 1
    else:
        valid = 0
    return valid

def initial_value(x: torch.Tensor, y: torch.Tensor, feature_num: int) -> float:
    """
    Given a dataset, target, and feature number, returns the expressivity of that feature over the dataset.
    :param x: the data tensor
    :param y: the target tensor
    :param feature_num: the feature to test
    :return: the float value of expressivity over the dataset
    """
    basis_list = [[0. for _ in range(x.shape[1])]]
    basis_list[0][feature_num] = 1.
    flat_list = [flatval for _ in range(x.shape[1])]

    if useCUDA:
        basis = torch.tensor(basis_list, requires_grad=True).cuda()
        flat = torch.tensor(flat_list, requires_grad=True).cuda()
        x_t = torch.t(x).cuda()
        denom = torch.inverse((x_t @ x) + torch.diag(flat)).cuda()
    else:
        basis = torch.tensor(basis_list, requires_grad=True)
        flat = torch.tensor(flat_list, requires_grad=True)
        x_t = torch.t(x)
        denom = torch.inverse((x_t @ x) + torch.diag(flat))
    return (basis @ (denom @ (x_t @ y))).item()

def final_value(x_0: torch.Tensor, y: torch.Tensor, params: torch.Tensor, feature_num: int):
    """
    Given a defined subgroup function, returns the expressivity over the test data set
    :param x_0: the test data tensor
    :param y: the test target tensor
    :param params: tensor with coefficients defining the subgroup
    :param feature_num: the feature to test
    :return: the float value of expressivity over the dataset and subgroup assignments
    """
    x = remove_intercept_column(x_0)
    basis_list = [[0. for _ in range(x.shape[1])]]
    basis_list[0][feature_num] = 1.
    flat_list = [flatval for _ in range(x.shape[1])]

    if useCUDA:
        basis = torch.tensor(basis_list, requires_grad=True).cuda()
        flat = torch.tensor(flat_list, requires_grad=True).cuda()
        params = torch.tensor(params, requires_grad=True, device="cuda")
        x_t = torch.t(x).cuda()
    else:
        basis = torch.tensor(basis_list, requires_grad=True)
        flat = torch.tensor(flat_list, requires_grad=True)
        x_t = torch.t(x)

    one_d = sigmoid(x_0 @ params)
    diag = torch.diag(one_d)
    denom = torch.inverse((x_t @ diag @ x) + torch.diag(flat))
    return (basis @ (denom @ (x_t @ diag @ y))).cpu().detach().numpy()[0], one_d.cpu().detach().numpy()

def find_extreme_subgroups(dataset: pd.DataFrame, alpha: list, target_column: str, f_sensitive: list, t_split: float):
    """
    Given a dataset, finds the differential expressivity and maximal subset over all features.
    Saves that subset to a file.
    :param dataset: the pandas dataframe to use
    :param alpha: desired subgroup size
    :param target_column:  Which column in that dataframe is the target.
    :param f_sensitive: Which features are sensitive characteristics
    :return:  N/A.  Logs results.
    """
    out_df = pd.DataFrame()

    train_df, test_df = train_test_split(dataset, test_size=t_split, random_state=seed)

    if useCUDA:
        y_train = torch.tensor(train_df[target_column].values).float().cuda()
        x_train = torch.tensor(train_df.drop(target_column, axis=1).values.astype('float16')).float().cuda()
        y_test = torch.tensor(test_df[target_column].values).float().cuda()
        x_test = torch.tensor(test_df.drop(target_column, axis=1).values.astype('float16')).float().cuda()
    else:
        y_train = torch.tensor(train_df[target_column].values).float()
        x_train = torch.tensor(train_df.drop(target_column, axis=1).values.astype('float16')).float()
        y_test = torch.tensor(test_df[target_column].values).float()
        x_test = torch.tensor(test_df.drop(target_column, axis=1).values.astype('float16')).float()
    errors_and_weights = []
    for feature_num in range(x_train.shape[1]-1):
        print("Feature", feature_num, "of", x_train.shape[1]-1)
        x_train_ni = remove_intercept_column(x_train)
        total_exp_train = initial_value(x_train_ni, y_train, feature_num)
        try:
            _, assigns_min, params_min, s_record_min, p_record_min = train_and_return(x_train, y_train, feature_num,
                                                                                      f_sensitive, alpha, minimize=True)
            _, assigns_max, params_max, s_record_max, p_record_max = train_and_return(x_train, y_train, feature_num,
                                                                                      f_sensitive, alpha, minimize=False)

            furthest_exp_min, _ = final_value(x_train, y_train, params_min, feature_num)
            furthest_exp_max, _ = final_value(x_train, y_train, params_max, feature_num)
            valid_min = is_valid(assigns_min, alpha)
            valid_max = is_valid(assigns_max, alpha)
            if valid_max * abs(furthest_exp_max - total_exp_train) > valid_min * abs(furthest_exp_min - total_exp_train):
                assigns_train, params, s_record, p_record = assigns_max, params_max, s_record_max, p_record_max
                furthest_exp_train = furthest_exp_max
            else:
                assigns_train, params, s_record, p_record = assigns_min, params_min, s_record_min, p_record_min
                furthest_exp_train = furthest_exp_min
            subgroup_size_train = np.mean(assigns_train)

            x_test_ni = remove_intercept_column(x_test)
            total_exp = initial_value(x_test_ni, y_test, feature_num)
            furthest_exp, assigns = final_value(x_test, y_test, params, feature_num)
            subgroup_size = np.mean(assigns)
            errors_and_weights.append((furthest_exp, feature_num))
            print(furthest_exp, feature_num)
            params_with_labels = {dataset.columns[i]: float(param) for (i, param) in enumerate(params)}
            out_df = pd.concat([out_df, pd.DataFrame.from_records([{'Feature': dataset.columns[feature_num],
                                                                    'Alpha': alpha,
                                                                    'F(D)': total_exp,
                                                                    'max(F(S))': furthest_exp,
                                                                    'Difference': abs(furthest_exp - total_exp),
                                                                    'Percent Change': 100*abs(furthest_exp - total_exp)/total_exp,
                                                                    'Subgroup Coefficients': params_with_labels,
                                                                    'Subgroup Size': subgroup_size,
                                                                    'F(D)_train': total_exp_train,
                                                                    'max(F(S))_train': furthest_exp_train,
                                                                    'Difference_train': abs(furthest_exp_train - total_exp_train),
                                                                    'Percent Change_train': 100*abs(furthest_exp_train - total_exp_train)/total_exp_train,
                                                                    'Subgroup Size_train': subgroup_size_train,
                                                                    'Size record': s_record,
                                                                    'WLS Penalties': p_record}])])
        except RuntimeError as e:
            print(e)
            continue
    errors_sorted = sorted(errors_and_weights, key=lambda elem: abs(elem[0]), reverse=True)
    print(errors_sorted[0])
    #i_value = initial_value(x, y, errors_sorted[0][1])
    #error, assigns, params = train_and_return(x, y, errors_sorted[0][1], i_value, f_sensitive, seed)
    #print(error, assigns[(assigns >= 0.002) & (assigns <= 1.0)])
    # params_with_labels = np.array(
    #     sorted([[dataset.columns[i], float(param)] for i, param in enumerate(params)], key=lambda row: abs(row[1]),
    #            reverse=True))
    # print(params_with_labels)
    print(dataset.columns[errors_sorted[0][1]])

    return out_df

def remove_intercept_column(x):
    mask = torch.arange(0, x.shape[1] - 1)
    x_cpu = x.cpu()
    out = torch.index_select(x_cpu, 1, mask)
    if useCUDA:
        out = out.cuda()
    return out

def run_system(df, target, sensitive_features, df_name, dummy=False, t_split=.5):
    if dummy:
        df[target] = df[target].sample(frac=1).values
        df_name = 'dummy_' + df_name

    # Add intercept column at the end
    df['Intercept'] = np.ones(df.shape[0])

    # Sort columns so target is at end
    new_cols = [col for col in df.columns if col != target] + [target]
    df = df[new_cols]

    # Get indices of sensitive features. Append a 1 for intercept
    f_sensitive = list(df.columns.get_indexer(sensitive_features))
    f_sensitive.append(df.shape[1]-2)

    print(df.shape[1])
    final_df = pd.DataFrame()

    start = time.time()
    #alphas = [[.01,.05],[.05,.1],[.1,.15],[.15,.2]]
    alphas = [[.1,.15]]
    for a in alphas:
        print("Running", df_name, ", Alpha =", a)
        out = find_extreme_subgroups(df, alpha=a, target_column=target, f_sensitive=f_sensitive, t_split=t_split)
        final_df = pd.concat([final_df, out])

    date = datetime.today().strftime('%m_%d')
    fname = f'output/nonsep/{df_name}_output_{date}.csv'
    final_df.to_csv(fname)
    print("Runtime:", '%.2f'%((time.time()-start)/3600), "Hours")
    return 1

seed = 0

df = pd.read_csv('data/student/student_cleaned.csv')
target = 'G3'
t_split = .5
sensitive_features = ['sex_M', 'Pstatus_T', 'address_U', 'Dalc', 'Walc', 'health']
df_name = 'student'
run_system(df, target, sensitive_features, df_name, dummy, t_split)

# df = pd.read_csv('data/compas/compas_recid.csv')
# target = 'two_year_recid'
# t_split = .5
# sensitive_features = ['age','sex_Male','race_African-American','race_Asian','race_Caucasian','race_Hispanic','race_Native American','race_Other']
# df_name = 'compas_recid'
# run_system(df, target, sensitive_features, df_name, dummy, t_split)

# df = pd.read_csv('data/compas/compas_decile.csv')
# target = 'decile_score'
# t_split = .5
# sensitive_features = ['age','sex_Male','race_African-American','race_Asian','race_Caucasian','race_Hispanic','race_Native American','race_Other']
# df_name = 'compas_decile'
# run_system(df, target, sensitive_features, df_name, dummy, t_split)

# df = BankDataset().convert_to_dataframe()[0]
# target = 'y'
# t_split = .2
# sensitive_features = ['age', 'marital=married', 'marital=single', 'marital=divorced']
# df_name = 'bank'
# run_system(df, target, sensitive_features, df_name, dummy, t_split)

# df = pd.read_csv('data/folktables/ACSIncome_MI_2018_new.csv')
# target = 'PINCP'
# t_split = .2
# sensitive_features = ['AGEP', 'SEX', 'MAR_1.0', 'MAR_2.0', 'MAR_3.0', 'MAR_4.0', 'MAR_5.0', 'RAC1P_1.0', 'RAC1P_2.0',
#                       'RAC1P_3.0', 'RAC1P_4.0', 'RAC1P_5.0', 'RAC1P_6.0', 'RAC1P_7.0', 'RAC1P_8.0', 'RAC1P_9.0']
# df_name = 'folktables'
# run_system(df, target, sensitive_features, df_name, dummy, t_split)

