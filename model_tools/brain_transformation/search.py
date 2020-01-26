import os
from collections import OrderedDict

from brainio_base.assemblies import BehavioralAssembly
from brainscore.model_interface import BrainModel
from candidate_models.base_models import BaseModelPool
from candidate_models.model_commitments.vs_layer import visual_search_layer

import cv2
import numpy as np
from tqdm import tqdm

class VisualSearchObjArray(BrainModel):
    def __init__(self, identifier, target_layer, stimulus_layer):
        self.current_task = None
        self.eye_res = 224
        self.arr_size = 6
        self.data_len = 300
        self.identifier = identifier

        self.fix = [[640, 512],
                     [365, 988],
                     [90, 512],
                     [365, 36],
                     [915, 36],
                     [1190, 512],
                     [915, 988]]

        target_model_pool = BaseModelPool(input_size=28)
        stimulus_model_pool = BaseModelPool(input_size=224)
        self.target_model = target_model_pool[identifier]
        self.stimuli_model = stimulus_model_pool[identifier]

        if target_layer==None:
            self.target_layer = visual_search_layer[identifier][0]
            self.stimuli_layer = visual_search_layer[identifier][0]
        else:
            self.target_layer = target_layer
            self.stimuli_layer = stimulus_layer


    def start_task(self, task: BrainModel.Task):
        self.current_task = task
        print(task, "started")

    def look_at(self, stimuli_set):
        self.gt_array = []
        gt = stimuli_set[stimuli_set['image_label'] == 'mask']
        gt_paths = list(gt.image_paths.values())[int(gt.index.values[0]):int(gt.index.values[-1]+1)]

        for i in range(6):
            imagename_gt = gt_paths[i]

            gt = cv2.imread(imagename_gt, 0)
            gt = cv2.resize(gt, (self.eye_res, self.eye_res), interpolation = cv2.INTER_AREA)
            retval, gt = cv2.threshold(gt, 125, 255, cv2.THRESH_BINARY)
            temp_stim = np.uint8(np.zeros((3*self.eye_res, 3*self.eye_res)))
            temp_stim[self.eye_res:2*self.eye_res, self.eye_res:2*self.eye_res] = np.copy(gt)
            gt = np.copy(temp_stim)
            gt = gt/255

            self.gt_array.append(gt)

        self.gt_total = np.copy(self.gt_array[0])
        for i in range(1,6):
            self.gt_total += self.gt_array[i]

        self.score = np.zeros((self.data_len, self.arr_size+1))
        self.data = np.zeros((self.data_len, self.arr_size+2, 2), dtype=int)
        S_data = np.zeros((300, 7, 2), dtype=int)
        I_data = np.zeros((300, 1), dtype=int)

        data_cnt = 0

        target = stimuli_set[stimuli_set['image_label'] == 'target']
        target_features = self.target_model(target, layers=[self.target_layer], stimuli_identifier=False)
        if target_features.shape[0] == target_features['neuroid_num'].shape[0]:
            target_features = target_features.T

        stimuli = stimuli_set[stimuli_set['image_label'] == 'stimuli']
        stimuli_features = self.stimuli_model(stimuli, layers=[self.stimuli_layer], stimuli_identifier=False)
        if stimuli_features.shape[0] == stimuli_features['neuroid_num'].shape[0]:
            stimuli_features = stimuli_features.T

        print(stimuli_features.shape, self.stimuli_layer, target_features.shape, self.target_layer)

        import torch

        for i in tqdm(range(self.data_len)):
            op_target = self.unflat(target_features[i:i+1])
            MMconv = torch.nn.Conv2d(op_target.shape[1], 1, kernel_size=op_target.shape[2], stride=1, bias=False)
            MMconv.weight = torch.nn.Parameter(torch.Tensor(op_target))

            gt_idx = target_features.tar_obj_pos.values[i]
            gt = self.gt_array[gt_idx]

            op_stimuli = self.unflat(stimuli_features[i:i+1])
            out = MMconv(torch.Tensor(op_stimuli)).detach().numpy()
            out = out.reshape(out.shape[2:])

            out = out - np.min(out)
            out = out/np.max(out)
            out *= 255
            out = np.uint8(out)
            out = cv2.resize(out, (self.eye_res, self.eye_res), interpolation = cv2.INTER_AREA)
            out = cv2.GaussianBlur(out,(7,7),3)

            temp_stim = np.uint8(np.zeros((3*self.eye_res, 3*self.eye_res)))
            temp_stim[self.eye_res:2*self.eye_res, self.eye_res:2*self.eye_res] = np.copy(out)
            attn = np.copy(temp_stim*self.gt_total)

            saccade = []
            (x, y) = int(attn.shape[0]/2), int(attn.shape[1]/2)
            saccade.append((x, y))

            for k in range(self.arr_size):
                (x, y) = np.unravel_index(np.argmax(attn), attn.shape)

                fxn_x, fxn_y = x, y

                fxn_x, fxn_y = max(fxn_x, self.eye_res), max(fxn_y, self.eye_res)
                fxn_x, fxn_y = min(fxn_x, (attn.shape[0]-self.eye_res)), min(fxn_y, (attn.shape[1]-self.eye_res))

                saccade.append((fxn_x, fxn_y))

                attn, t = self.remove_attn(attn, saccade[-1][0], saccade[-1][1])

                if(t==gt_idx):
                    self.score[data_cnt, k+1] = 1
                    data_cnt += 1
                    break

            saccade = np.asarray(saccade)
            j = saccade.shape[0]

            for k in range(j):
                tar_id = self.get_pos(saccade[k, 0], saccade[k, 1], 0)
                saccade[k, 0] = self.fix[tar_id][0]
                saccade[k, 1] = self.fix[tar_id][1]

            I_data[i, 0] = min(7, j)
            S_data[i, :j, 0] = saccade[:, 0].reshape((-1,))[:7]
            S_data[i, :j, 1] = saccade[:, 1].reshape((-1,))[:7]

            self.data[:,:7,:] = S_data
            self.data[:,7,:] = I_data

        return (self.score, self.data)

    def remove_attn(self, img, x, y):
        t = -1
        for i in range(5, -1, -1):
            fxt_place = self.gt_array[i][x, y]
            if (fxt_place>0):
                t = i
                break

        if(t>-1):
            img[self.gt_array[t] == 1] = 0

        return img, t

    def get_pos(self, x, y, t):
        for i in range(5, -1, -1):
            fxt_place = self.gt_array[i][int(x), int(y)]
            if (fxt_place>0):
                t = i + 1
                break
        return t

    def unflat(self, X):
        channel_names = ['channel', 'channel_x', 'channel_y']
        assert all(hasattr(X, coord) for coord in channel_names)
        shapes = [len(set(X[channel].values)) for channel in channel_names]
        X = np.reshape(X.values, [X.shape[0]] + shapes)
        X = np.transpose(X, axes=[0, 3, 1, 2])
        return X
