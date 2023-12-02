# Data pre-processing and data loader generation.

import copy
import time
import os
import pickle
import jieba
from fastprogress import progress_bar
from ckonlpy.tag import Twitter
twitter = Twitter()

import torch
from torch.utils.data import Dataset, DataLoader


def NML(seg_sents, mention_positions, ws):
    """
    Nearest Mention Location
    
    params
        seg_sents: segmented sentences of an instance in a list.
            [[word 1,...] of sentence 1,...].
        mention_positions: the positions of mentions of a candidate.
            [[sentence-level index, word-level index] of mention 1,...].
        ws: single-sided context window size.

    return
        The position of the mention which is the nearest to the quote.
    """
    def word_dist(pos):
        """
        The word level distance between quote and the mention position

        param
            pos: [sentence-level index, word-level index] of the character mention.

        return
            w_d: word-level distance between the mention and the quote.
        """
        if pos[0] == ws:
            w_d = ws * 2
        elif pos[0] < ws:
            w_d = sum(len(sent) for sent in seg_sents[pos[0] + 1:ws]) + len(seg_sents[pos[0]][pos[1] + 1:])
        else:
            w_d = sum(len(sent) for sent in seg_sents[ws + 1:pos[0]]) + len(seg_sents[pos[0]][:pos[1]])
        return w_d
    
    sorted_positions = sorted(mention_positions, key=lambda x: word_dist(x))

    # trick
    if seg_sents[ws - 1][-1] == '：':
        # if the preceding sentence ends with '：'
        for pos in sorted_positions:
            # search candidate mention from left-side context
            if pos[0] < ws:
                return pos

    return sorted_positions[0]


def seg_and_mention_location(raw_sents_in_list, alias2id):
    """
    Chinese word segmentation and candidate mention location.
    
    params
        raw_sents_in_list: unsegmented sentences of an instance in a list.
        alias2id: a dict mapping character alias to its ID.
    
    return
        seg_sents: segmented sentences of the input instance.
        character_mention_poses: a dict mapping the index of a candidate to its mention positions.
            {character index: [[sentence index, word index in sentence] of mention 1,...]...}.
    """
    character_mention_poses = {}
    seg_sents = []

    for sent_idx, sent in enumerate(raw_sents_in_list):
        # sent = "“我知道哩，你这样情况，在咱县贷款是确有困难！”"
        # seg_sentc = list(jieba.cut(sent, cut_all=False))
        seg_sent = twitter.morphs(sent)
        # return print(seg_sent, seg_sentc, sent)
        for word_idx, word in enumerate(seg_sent):
            if word in alias2id:
                if alias2id[word] in character_mention_poses:
                    character_mention_poses[alias2id[word]].append([sent_idx, word_idx])
                else:
                    character_mention_poses[alias2id[word]] = [[sent_idx, word_idx]]
        seg_sents.append(seg_sent)
    return seg_sents, character_mention_poses


def create_CSS(seg_sents, candidate_mention_poses, ws, max_len):
    """
    Create candidate-specific segments for each candidate in an instance.

    params
        seg_sents: 2ws + 1 segmented sentences in a list.
        candidate_mention_poses: a dict which contains the position of candiate mentions,
            with format {character index: [[sentence index, word index in sentence] of mention 1,...]...}.
        ws: single-sided context window size.
        max_len: maximum length limit.

    return
        Returned contents are in lists, in which each element corresponds to a candidate.
        The order of candidate is consistent with that in list(candidate_mention_poses.keys()).
        many_CSS: candidate-specific segments.
        many_sent_char_len: segmentation information of candidate-specific segments.
            [[character-level length of sentence 1,...] of the CSS of candidate 1,...].
        many_mention_pos: the position of the nearest mention in CSS. 
            [(sentence-level index of nearest mention in CSS, 
             character-level index of the leftmost character of nearest mention in CSS, 
             character-level index of the rightmost character + 1) of candidate 1,...].
        many_quote_idx: the sentence-level index of quote sentence in CSS.

    """

    assert len(seg_sents) == ws * 2 + 1

    def max_len_cut(seg_sents, mention_pos):
        """
        Cut the CSS of each candidate to fit the maximum length limitation.

        params
            seg_sents: the segmented sentences involved in the CSS of a candidate.
            mention_pos: the position of the mention of the candidate in the CSS.

        return
            seg_sents: ... after truncated.
            mention_pos: ... after truncated.
        """
        sent_char_lens = [sum(len(word) for word in sent) for sent in seg_sents]
        sum_char_len = sum(sent_char_lens)

        running_cut_idx = [len(sent) - 1 for sent in seg_sents]

        while sum_char_len > max_len:
            max_len_sent_idx = max(list(enumerate(sent_char_lens)), key=lambda x: x[1])[0]

            if max_len_sent_idx == mention_pos[0] and running_cut_idx[max_len_sent_idx] == mention_pos[1]:
                running_cut_idx[max_len_sent_idx] -= 1

            if max_len_sent_idx == mention_pos[0] and running_cut_idx[max_len_sent_idx] < mention_pos[1]:
                mention_pos[1] -= 1

            reduced_char_len = len(seg_sents[max_len_sent_idx][running_cut_idx[max_len_sent_idx]])
            sent_char_lens[max_len_sent_idx] -= reduced_char_len
            sum_char_len -= reduced_char_len

            del seg_sents[max_len_sent_idx][running_cut_idx[max_len_sent_idx]]

            running_cut_idx[max_len_sent_idx] -= 1

        return seg_sents, mention_pos

    many_CSSs = []
    many_sent_char_lens = []
    many_mention_poses = []
    many_quote_idxes = []
    many_cut_css = []

    for candidate_idx in candidate_mention_poses.keys():

        nearest_pos = NML(seg_sents, candidate_mention_poses[candidate_idx], ws)
        if nearest_pos[0] <= ws:
            CSS = copy.deepcopy(seg_sents[nearest_pos[0]:ws + 1])
            mention_pos = [0, nearest_pos[1]]
            quote_idx = ws - nearest_pos[0]
        else:
            CSS = copy.deepcopy(seg_sents[ws:nearest_pos[0] + 1])
            mention_pos = [nearest_pos[0] - ws, nearest_pos[1]]
            quote_idx = 0

        cut_CSS, mention_pos = max_len_cut(CSS, mention_pos)

        # print('cut_Css 확인', cut_CSS)

        sent_char_lens = [sum(len(word) for word in sent) for sent in cut_CSS]

        # print(sent_char_lens)
        # print('mention pos1', mention_pos)
        
        mention_pos_left = sum(sent_char_lens[:mention_pos[0]]) + sum(len(x) for x in cut_CSS[mention_pos[0]][:mention_pos[1]])
        mention_pos_right = mention_pos_left + len(cut_CSS[mention_pos[0]][mention_pos[1]])
        mention_pos = (mention_pos[0], mention_pos_left, mention_pos_right, mention_pos[1])
        cat_CSS = ''.join([''.join(sent) for sent in cut_CSS])

        # print('mention pos2', mention_pos)

        many_CSSs.append(cat_CSS)
        many_sent_char_lens.append(sent_char_lens)
        many_mention_poses.append(mention_pos)
        many_quote_idxes.append(quote_idx)
        many_cut_css.append(cut_CSS)

    return many_CSSs, many_sent_char_lens, many_mention_poses, many_quote_idxes, many_cut_css


class ISDataset(Dataset):
    """
    Dataset subclass for Identifying speaker.
    """
    def __init__(self, data_list):
        super(ISDataset, self).__init__()
        self.data = data_list

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return self.data[idx]


def build_data_loader(data_file, alias2id, args, skip_only_one=False):
    """
    Build the dataloader for training.

    Input:
        data_file: labelled training data as in https://github.com/YueChenkkk/Chinese-Dataset-Speaker-Identification.
        name_list_path: the path of the name list which contains the aliases of characters.
        args: parsed arguments.
        skip_only_one: a flag for filtering out the instances that have only one candidate, such 
            instances have no effect while training.

    Output:
        A torch.utils.data.DataLoader object which generates:
            raw_sents_in_list: the raw (unsegmented) sentences of the instance.
                [sentence -ws, ..., qs, ..., sentence ws].
            CSSs: candidate-specific segments for candidates.
                [CSS of candidate 1,...].
            sent_char_lens: the character length of each sentence in the instance.
                [[character-level length of sentence 1,...] in the CSS of candidate 1,...].
            mention_poses: positions of mentions in the concatenated sentences.
                [(sentence-level index of nearest mention in CSS, 
                 character-level index of the leftmost character of nearest mention in CSS, 
                 character-level index of the rightmost character + 1) of candidate 1,...]
            quote_idxes: quote index in CSS of candidates in list.
            one_hot_label: one-hot label of the true speaker on list(mention_poses.keys()).
            true_index: index of the speaker on list(mention_poses.keys()).
    """
    # twitter = Twitter()
    for alias in alias2id:
        twitter.add_dictionary(alias, 'Noun')
        # return(print(okt.add_dictionary(alias, 'Noun')))
        # return print(jieba.add_word(alias), alias, alias2id)

    # load instances from file
    with open(data_file, 'r', encoding='utf-8') as fin:
        data_lines = fin.readlines()
        # print('data_lines', data_lines)

    # pre-processing
    data_list = []

    # print('data_lines', len(data_lines))
    for i, line in enumerate(progress_bar(data_lines, total=len(data_lines))):
        offset = i % 26
        
        if offset == 0:
            raw_sents_in_list = []
            continue

        if offset < 22:
            raw_sents_in_list.append(line.strip())
            # print('그냥 line', line)
            # print('전처리 line', line.strip())
            # raw_sents_in_list.append(line)

        if offset == 22:
            speaker_name = line.strip().split()[-1]
            # segmentation and character mention location
            # print('전에 어떤것이 들어갈까여 - raw_sents_in_list', raw_sents_in_list)
            # print('전에 어떤것이 들어갈까여2 - alias2id', alias2id)
            seg_sents, candidate_mention_poses = seg_and_mention_location(raw_sents_in_list, alias2id)
            # print('결과 보져 - seg_sents', seg_sents)
            # print('결과 보져 2 - candidate_mention_poses', candidate_mention_poses)
            if skip_only_one and len(candidate_mention_poses) == 1:
                continue
            CSSs, sent_char_lens, mention_poses, quote_idxes, cut_css = create_CSS(seg_sents, 
                                                                          candidate_mention_poses, 
                                                                          args.ws, 
                                                                          args.length_limit)
            # print('여기 CSS 가 있습니다', CSSs)
            one_hot_label = [0 if character_idx != alias2id[speaker_name] else 1 
                             for character_idx in candidate_mention_poses.keys()]
            true_index = one_hot_label.index(1) if 1 in one_hot_label else 0

        if offset == 24:
            category = line.strip().split()[-1]
            data_list.append((seg_sents, CSSs, sent_char_lens, mention_poses, 
                              quote_idxes, cut_css, one_hot_label, true_index, category))

    return DataLoader(ISDataset(data_list), batch_size=1, collate_fn=lambda x: x[0])
