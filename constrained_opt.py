from lime_exp_func import LimeExpFunc
from constrained_solver import ConstrainedSolver
from learner import Learner
from sklearn import linear_model
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
import pandas as pd
import numpy as np
from aif360.datasets import CompasDataset, BankDataset
import time
import argparse
from datetime import datetime
import json
import pdb


parser = argparse.ArgumentParser(description='Locally separable run')
parser.add_argument('--dummy', action='store_true')
args = parser.parse_args()
dummy = args.dummy


def argmin_g(x, y, feature_num, f_sensitive, exp_func, minimize):
    exp_order = np.mean([abs(exp_func.exps[i][feature_num]) for i in range(len(x))])
    solver = ConstrainedSolver(exp_func, alpha_s=.1, alpha_L=.5, B=10000*exp_order, nu=.00001)
    v = .01*exp_order

    x_sensitive = x[:,f_sensitive]
    costs0 = [0 for _ in range(len(x))] # costs0 is always zeros
    learner = Learner(x_sensitive, y, linear_model.LinearRegression())
    #learner = Learner(x, y, linear_model.LinearRegression())

    _ = 1
    while solver.v_t > v:
        if _%100==0:
            print("ITERATION NUMBER ", _)
            print(sum(assigns)/len(assigns))
            print(solver.v_t, ' | ',v)
        solver.update_lambdas()

        # CSC solver, returns regoracle fit using costs0/costs1
        # h_t <- Best_h(lam_t)
        current_lam = solver.lambda_history[-1]
        if minimize:
            costs1 = [exp_func.exps[i][feature_num]-current_lam[0]+current_lam[1] for i in range(len(x))]
        else:
            costs1 = [-exp_func.exps[i][feature_num]-current_lam[0]+current_lam[1] for i in range(len(x))]
        l_response = learner.best_response(costs0, costs1)
        solver.g_history.append(l_response)

        assigns, cost = l_response.predict(x_sensitive)
        expressivity = exp_func.get_total_exp(assigns, feature_num)
        solver.pred_history.append(np.array(assigns))
        solver.exp_history.append(expressivity)

        # Q^ <- avg(h_t), L_ceiling <- L(Q^, best_lam(Q^))
        avg_pred = [np.mean(k) for k in zip(*solver.pred_history)]
        best_lam = solver.best_lambda(avg_pred)
        L_ceiling = solver.lagrangian(avg_pred, best_lam, feature_num, minimize)

        # lam^ <- avg(lambda), L_floor <- L(best_h(lam^), lam^)
        avg_lam = [np.mean(k) for k in zip(*solver.lambda_history)]
        #print('avg_lam: ', avg_lam, ' | best_lam: ', best_lam, '| current lam: ', current_lam)
        best_g = solver.best_g(learner, feature_num, avg_lam, minimize)
        best_g_assigns, best_g_costs = best_g.predict(x_sensitive)
        best_g_exps = exp_func.get_total_exp(best_g_assigns, feature_num)
        L_floor = solver.lagrangian(best_g_assigns, avg_lam, feature_num, minimize)

        L = solver.lagrangian(avg_pred, avg_lam, feature_num, minimize)
        solver.v_t = max(L-L_floor, L_ceiling-L)
        #print(np.mean(assigns), np.mean(best_g_assigns), np.mean(avg_pred))
        #print(L, L_floor, L_ceiling)
        #solver.v_t = max(abs(L-L_floor), abs(L_ceiling-L))

        solver.update_thetas(assigns)
        _ += 1
    ### method 1: Returning average model
    # print('num iterations: ', _-1)
    # avg_pred = [np.mean(k) for k in zip(*solver.pred_history)]
    # avg_lam = [np.mean(k) for k in zip(*solver.lambda_history)]
    # final_expressivity = 0
    # for i in range(len(avg_pred)):
    #     final_expressivity += avg_pred[i]*exp_func.exps[i][feature_num]
    # return solver.g_history, avg_pred, final_expressivity

    ### method 2: Returning best valid model
    print('num iterations: ', _-1)
    best_model, best_assigns, best_exp = solver.get_best_valid_model(minimize)
    return best_model, best_assigns, best_exp

# Given distribution of models, compute predictions on x and return average
def get_avg_prediction(mix_models, x):
    predictions = [m.predict(x)[0] for m in mix_models]
    avg_pred = [np.mean(k) for k in zip(*predictions)]
    return avg_pred

def full_dataset_expressivity(exp_func, feature_num):
    total = 0
    for row in exp_func.exps:
        total += row[feature_num]
    return total

def split_out_dataset(dataset, target_column):
    x = dataset.drop(target_column, axis=1).to_numpy()
    y = dataset[target_column].to_numpy()
    #sensitive_ds = dataset[f_sensitive].to_numpy()
    return x, y


def extremize_exps_dataset(dataset, exp_func, target_column, f_sensitive, seed):
    """
    :param dataset: pandas dataframe
    :param exp_func: class for expressivities
    :param target_column: string, column name in dataset
    :param f_sensitive: list of column names that are sensitive features
    :param seed: int, random seed
    :return: total expressivity over these rows
    """
    if target_column == 'G3':
        t_split = .5
    else:
        t_split = .2
    train_df, test_df = train_test_split(dataset, test_size=t_split, random_state=seed)
    x_train, y_train = split_out_dataset(train_df, target_column)
    x_test, y_test = split_out_dataset(test_df, target)
    classifier = RandomForestClassifier(random_state=seed)
    classifier.fit(x_train, y_train)
    out_df = pd.DataFrame()

    exp_func_train = exp_func(classifier, x_train, seed)
    #print("Populating train expressivity values")
    #exp_func_train.populate_exps()

    exp_func_test = exp_func(classifier, x_test, seed)
    #print("Populating test expressivity values")
    #exp_func_test.populate_exps()

    # Temporary exp populating
    with open(f'data/exps/{df_name}_train_seed0', 'r') as f:
        train_temp = list(map(json.loads, f))[0]
    for e_list in train_temp:
        exp_func_train.exps.append({int(k):v for k,v in e_list.items()})
    with open(f'data/exps/{df_name}_test_seed0', 'r') as f:
        test_temp = list(map(json.loads, f))[0]
    for e_list in test_temp:
        exp_func_test.exps.append({int(k):v for k,v in e_list.items()})
    # ^Temporary code, delete later^

    for feature_num in range(len(x_train[0])):
        print('*****************')
        print(train_df.columns[feature_num])
        total_exp_train = full_dataset_expressivity(exp_func_train, feature_num)
        print('total exp: ', total_exp_train)
        min_model, min_assigns, min_exp = argmin_g(x_train, y_train, feature_num, f_sensitive, exp_func_train,
                                                   minimize=True)
        print('min exp', min_exp, '| size', sum(min_assigns) / len(min_assigns))
        max_model, max_assigns, max_exp = argmin_g(x_train, y_train, feature_num, f_sensitive, exp_func_train,
                                                   minimize=False)
        print('max exp', max_exp, '| size', sum(max_assigns)/len(max_assigns))

        # Choose max difference
        if abs(max_exp-total_exp_train) > abs(min_exp-total_exp_train):
            furthest_exp_train = max_exp
            assigns_train = max_assigns
            best_model = max_model
            direction = 'maximize'
        else:
            furthest_exp_train = min_exp
            assigns_train = min_assigns
            best_model = min_model
            direction = 'minimize'
        subgroup_size_train = np.mean(assigns_train)

        # compute test values
        total_exp_test = full_dataset_expressivity(exp_func_test, feature_num)
        #assigns_test = get_avg_prediction(best_model, x_test) # mix model method
        assigns_test = best_model.predict(x_test[:,f_sensitive])[0] # sensitive features only method
        #assigns_test = best_model.predict(x_test)[0]
        subgroup_size_test = sum(assigns_test)/len(assigns_test)
        furthest_exp_test = 0
        for i in range(len(assigns_test)):
            furthest_exp_test += assigns_test[i]*exp_func_test.exps[i][feature_num]

        # # from mix models, pick model with largest exp diff that is valid
        params = best_model.b1.coef_
        params_with_labels = {dataset.columns[i]: float(param) for (i, param) in zip(f_sensitive, params)}
        print(params_with_labels)

        out_df = pd.concat([out_df, pd.DataFrame.from_records([{'Feature': dataset.columns[feature_num],
                                                                'F(D)': total_exp_test,
                                                                'max(F(S))': furthest_exp_test,
                                                                'Difference': abs(furthest_exp_test - total_exp_test),
                                                                'Subgroup Coefficients': params_with_labels,
                                                                'Subgroup Size': subgroup_size_test,
                                                                'Direction': direction,
                                                                'F(D)_train': total_exp_train,
                                                                'max(F(S))_train': furthest_exp_train,
                                                                'Difference_train': abs(furthest_exp_train-total_exp_train),
                                                                'Subgroup Size_train': subgroup_size_train}])])

    return out_df


def run_system(df, target, sensitive_features, df_name, dummy=False):
    if dummy:
        df[target] = df[target].sample(frac=1).values
        df_name = 'dummy_' + df_name

    # Sort columns so target is at end
    new_cols = [col for col in df.columns if col != target] + [target]
    df = df[new_cols]

    f_sensitive = list(df.columns.get_indexer(sensitive_features))

    seeds = [0]
    for s in seeds:
        np.random.seed(s)
        print("Running", df_name, ", Seed =", s)
        start = time.time()

        out = extremize_exps_dataset(dataset=df, exp_func=LimeExpFunc, target_column=target,
                                     f_sensitive=f_sensitive, seed=s)

        date = datetime.today().strftime('%m_%d')
        out.head()
        out.to_csv(f'output_constrained/{df_name}_output_seed{s}_{date}.csv')
        print("Runtime:", '%.2f'%((time.time()-start)/3600), "Hours")
    return 1


# df = pd.read_csv('data/student/student_cleaned.csv')
# target = 'G3'
# sensitive_features = ['sex_M', 'Pstatus_T', 'address_U', 'Dalc', 'Walc', 'health']
# # sensitive_features = list(df.columns).remove('G3')
# df_name = 'student'
# run_system(df, target, sensitive_features, df_name, dummy)

# df = pd.read_csv('data/compas/compas_cleaned.csv')
# target = 'two_year_recid'
# sensitive_features = ['age','sex_Male','race_African-American','race_Asian','race_Caucasian','race_Hispanic','race_Native American','race_Other']
# df_name = 'compas_recid'
# run_system(df, target, sensitive_features, df_name, dummy)

df = pd.read_csv('data/compas/compas_cleaned_decile.csv')
target = 'decile_score'
sensitive_features = ['age','sex_Male','race_African-American','race_Asian','race_Caucasian','race_Hispanic','race_Native American','race_Other']
df_name = 'compas_decile'
run_system(df, target, sensitive_features, df_name, dummy)