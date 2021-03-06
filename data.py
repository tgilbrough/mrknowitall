import json, math
import numpy as np
import os
import nltk
from tqdm import tqdm
import re
import string

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

class Data:
    def __init__(self, config):
        self.config = config
        self.batch_size = config.batch_size
        self.keep_prob = config.keep_prob
        self.valBatchNum = 0
        self.testBatchNum = 0

        if config.smart_unk:
            self.unknown_classes = [re.compile('\d+'), # contains a number
                                    re.compile('^[\s{}]+$'.format(re.escape(string.punctuation))), # punctuation
                                    re.compile('^[a-z]+$'), # misspelled word
                                    re.compile('.*')] # catch all
        else:
            self.unknown_classes = [re.compile('.*')]

        print('Preparing embedding matrix.')

        # load training data, parse, and split
        print('Loading in training data...')
        trainData = self.importMsmarco(config.train_path)
        self.tContext, self.tXLen, self.tQuestion, self.tXqLen, self.tQuestionID, self.tAnswerBegin, self.tAnswerEnd, self.tAnswerText, \
            self.maxLenTContext, self.maxLenTQuestion = self.splitMsmarcoDatasets(trainData)

        # load validation data, parse, and split
        print('Loading in validation data...')
        valData = self.importMsmarco(config.val_path)
        self.vContext, self.vXLen, self.vQuestion, self.vXqLen, self.vQuestionID, self.vAnswerBegin, self.vAnswerEnd, self.vAnswerText, \
            self.maxLenVContext, self.maxLenVQuestion = self.splitMsmarcoDatasets(valData)

        # load validation for eval data, parse, and split
        valEvalData = self.importMsmarco(config.val_path)
        self.vmContext, self.vmXLen, self.vmQuestion, self.vmXqLen, self.vmQuestionID, self.vmUrl, \
            self.maxLenVmContext, self.maxLenVmQuestion = self.splitMsmarcoDatasetsTest(valEvalData)

        # load test data, parse, and split
        print('Loading in testing data...')
        testData = self.importMsmarco(config.test_path)
        self.temContext, self.temXLen, self.teQuestion, self.teXqLen, self.teQuestionID, self.teUrl, \
            self.maxLenTeContext, self.maxLenTeQuestion = self.splitMsmarcoDatasetsTest(testData)

        print('Building vocabulary...')
        # build a vocabulary over all training and validation context paragraphs and question words
        vocab = self.buildVocab(self.tContext + self.tQuestion 
                                + [t for p in self.vmContext for t in p] + self.vQuestion
                                + [t for p in self.temContext for t in p] + self.teQuestion)

        self.vocab = vocab

        self.vocab_size = len(vocab)
        word_index = dict((c, i) for i, c in enumerate(vocab))
        self.max_context_size = max([self.maxLenTContext, self.maxLenVmContext, self.maxLenTeContext])
        self.max_ques_size = max([self.maxLenTQuestion, self.maxLenVmQuestion, self.maxLenTeQuestion])

        # Note: Need to download and unzip Glove pre-train model files into same file as this script
        embeddings_index = self.loadGloveModel('./datasets/glove/glove.6B.' + str(config.emb_size) + 'd.txt')
        # Cutting down word_index to only include words represented by GloVe embeddings, and updating vocab to only
        # GloVe words
        self.embeddings, word_index = self.createEmbeddingMatrix(embeddings_index, word_index)

        # Calculating passage relevance weights
        print('Calculating passage relevance weights...')
        self.vmPassWeight = self.passageRevelevance(self.vmContext, self.vmQuestion)
        self.temPassWeight = self.passageRevelevance(self.temContext, self.teQuestion)

        # vectorize training and validation datasets
        print('Begin vectorizing process...')

        # tX: training Context, tXq: training Question, tYBegin: training Answer Begin ptr,
        # tYEnd: training Answer End ptr
        print('Vectorizing train data...')
        self.tX, self.tXq, self.tYBegin, self.tYEnd = self.vectorizeData(self.tContext, self.tQuestion,
                                                     self.tAnswerBegin, self.tAnswerEnd, word_index,
                                                     self.max_context_size, self.max_ques_size)

        print('Vectorizing validation data...')
        self.vX, self.vXq, self.vYBegin, self.vYEnd = self.vectorizeData(self.vContext, self.vQuestion,
                                                     self.vAnswerBegin, self.vAnswerEnd, word_index,
                                                     self.max_context_size, self.max_ques_size)

        
        self.vmX, self.vmXq = self.vectorizeDataMutli(self.vmContext, self.vmQuestion, word_index,
                                                     self.max_context_size, self.max_ques_size)

        print('Vectorizing test data...')
        self.temX, self.teXq = self.vectorizeDataMutli(self.temContext, self.teQuestion, word_index,
                                                     self.max_context_size, self.max_ques_size)

        print('Vectorizing process completed.')

        self.convertToNumpy()

    def convertToNumpy(self):
        self.tX = np.array(self.tX)
        self.tXLen = np.array(self.tXLen)
        self.tXq = np.array(self.tXq)
        self.tXqLen = np.array(self.tXqLen)
        self.tYBegin = np.array(self.tYBegin)
        self.tYEnd = np.array(self.tYEnd)

        self.vX = np.array(self.vX)
        self.vXLen = np.array(self.vXLen)
        self.vmX = np.array(self.vmX)
        self.vmXLen = np.array(self.vmXLen)
        self.vXq = np.array(self.vXq)
        self.vmXq = np.array(self.vmXq)
        self.vXqLen = np.array(self.vXqLen)
        self.vYBegin = np.array(self.vYBegin)
        self.vYEnd = np.array(self.vYEnd)
        self.vContext = np.array(self.vContext, dtype=object)
        self.vmContext = np.array(self.vmContext, dtype=object)
        self.vQuestionID = np.array(self.vQuestionID, dtype=object)
        self.vmQuestionID = np.array(self.vmQuestionID, dtype=object)
        self.vmUrl = np.array(self.vmUrl, dtype=object)
        self.vmPassWeight = np.array(self.vmPassWeight)

        self.temX = np.array(self.temX)
        self.temXLen = np.array(self.temXLen)
        self.teXq = np.array(self.teXq)
        self.teXqLen = np.array(self.teXqLen)
        self.temContext = np.array(self.temContext, dtype=object)
        self.teQuestionID = np.array(self.teQuestionID, dtype=object)
        self.temPassWeight = np.array(self.temPassWeight)
        self.teUrl = np.array(self.teUrl, dtype=object)


    def getNumTrainBatches(self):
        return int(math.ceil(len(self.tX) / self.batch_size))

    def getNumValBatches(self):
        return int(math.ceil(len(self.vX) / self.batch_size))

    def getNumTestBatches(self):
        return int(math.ceil(len(self.temX) / self.batch_size))

    def getRandomTrainBatch(self):
        points = np.random.choice(len(self.tX), self.batch_size)

        tX_batch = self.tX[points]
        tXLen_batch = self.tXLen[points]
        tXq_batch = self.tXq[points]
        tXqLen_batch = self.tXqLen[points]
        tYBegin_batch = self.tYBegin[points]
        tYEnd_batch = self.tYEnd[points]

        return {'tX': tX_batch, 'tXLen': tXLen_batch, 'tXq': tXq_batch,
                'tXqLen': tXqLen_batch, 'tYBegin': tYBegin_batch, 'tYEnd': tYEnd_batch}

    def getRandomValBatch(self):
        points = np.random.choice(len(self.vX), self.batch_size)

        vX_batch = self.vX[points]
        vXLen_batch = self.vXLen[points]
        vXq_batch = self.vXq[points]
        vXqLen_batch = self.vXqLen[points]
        vYBegin_batch = self.vYBegin[points]
        vYEnd_batch = self.vYEnd[points]

        return {'vX': vX_batch, 'vXLen': vXLen_batch, 'vXq': vXq_batch,
                'vXqLen':  vXqLen_batch, 'vYBegin': vYBegin_batch, 'vYEnd': vYEnd_batch}

    def getValBatch(self):
        start = self.valBatchNum * self.batch_size
        end = min(len(self.vX), (self.valBatchNum + 1) * self.batch_size)
        points = np.arange(start, end)

        vmContext_batch = self.vmContext[points]
        vmQuestionID_batch = self.vmQuestionID[points]
        vmX_batch = self.vmX[points]
        vmXLen_batch = self.vmXLen[points]
        vmXq_batch = self.vmXq[points]
        vmXqLen_batch = self.vXqLen[points]
        vmUrl_batch = self.vmUrl[points]
        vmXPassWeights_batch = self.vmPassWeight[points]

        self.valBatchNum += 1

        if self.valBatchNum >= self.getNumValBatches():
            self.valBatchNum = 0

        return {'vmContext': vmContext_batch, 'vmQuestionID': vmQuestionID_batch,
                'vmX': vmX_batch, 'vmXLen': vmXLen_batch, 'vmXq': vmXq_batch, 'vmXqLen': vmXqLen_batch,
                'vmUrl': vmUrl_batch, 'vmXPassWeight': vmXPassWeights_batch}

    def getTestBatch(self):
        start = self.testBatchNum * self.batch_size
        end = min(len(self.temX), (self.testBatchNum + 1) * self.batch_size)
        points = np.arange(start, end)

        temContext_batch = self.temContext[points]
        teQuestionID_batch = self.teQuestionID[points]
        temX_batch = self.temX[points]
        temXLen_batch = self.temXLen[points]
        teXq_batch = self.teXq[points]
        teXqLen_batch = self.teXqLen[points]
        teUrl_batch = self.teUrl[points]
        temXPassWeight_batch = self.temPassWeight[points]

        self.testBatchNum += 1

        if self.testBatchNum >= self.getNumTestBatches():
            self.testBatchNum = 0

        return {'temContext': temContext_batch, 'teQuestionID': teQuestionID_batch,
                'temX': temX_batch, 'temXLen': temXLen_batch, 'teXq': teXq_batch, 'teXqLen': teXqLen_batch,
                'teUrl': teUrl_batch, 'temXPassWeight': temXPassWeight_batch}

    def loadGloveModel(self, gloveFile):
        print("Loading Glove Model...")
        f = open(gloveFile,'r', encoding='utf-8')
        embedding_index = {}
        for line in f:
            values = line.split()
            word = values[0]
            coefs = np.asarray(values[1:], dtype='float32')
            embedding_index[word] = coefs
        f.close()
        print('Found %s word vectors.' % len(embedding_index))
        return embedding_index

    def createEmbeddingMatrix(self, embeddings_index, word_index):
        dim_size = len(embeddings_index['a'])

        embedding_matrix = []
        new_word_index = {}
        unknown = set()
        self.vocab = []
        for word, i in word_index.items():
            embedding_vector = embeddings_index.get(word)
            if embedding_vector is not None:
                # words not found in embedding index will be all-zeros.
                embedding_matrix.append(embedding_vector)
                new_word_index[word] = len(embedding_matrix) - 1
                self.vocab.append(word)
            else:
                unknown.add(word)

        self.vocab_size = len(self.vocab)
        embedding_matrix = np.asarray(embedding_matrix)

        # print(unknown)
        print('Number of Unknown Words:', len(unknown))
        return embedding_matrix, new_word_index

    def tokenize(self, sent):
        '''Return the tokens of a context including punctuation. Wrapper around nltk.word_tokenize to
           fix weird quotation marks.
        '''
        tokens = []
        for token in nltk.word_tokenize(sent):
            token = token.replace("``", '"').replace("''", '"').replace('’', "'").replace('‘', "'").replace('”', '"').replace('“', '"')
            tokens.append(token)
        return tokens

    def join(self, sent):
        def join_punctuation(seq, characters='.,;?!'):
            characters = set(characters)
            seq = iter(seq)
            current = next(seq)

            for nxt in seq:
                if nxt in characters:
                    current += nxt
                else:
                    yield current
                    current = nxt

            yield current
        return ' '.join(join_punctuation(sent))

    def splitMsmarcoDatasets(self, f):
        '''Given a parsed Json data object, split the object into training context (paragraph), question, answer matrices,
           and keep track of max context and question lengths.
        '''
        xContext = [] # list of contexts paragraphs
        xQuestion = [] # list of questions
        xQuestionID = [] # list of question id
        xAnswerBegin = [] # list of indices of the beginning word in each answer span
        xAnswerEnd = [] # list of indices of the ending word in each answer span
        xAnswerText = [] # list of the answer text
        xLen = [] # list of unpadded lengths
        qLen = [] # list of unpadded lengths
        maxLenContext = 0
        maxLenQuestion = 0

        # For now only pick out selected passages that have answers directly inside the passage
        for data in f['data']:
            for passage in data['passages']:
                if passage['is_selected'] == 0:
                    continue
                context = passage['passage_text']
                context = context.replace("''", '" ')
                context = context.replace("``", '" ')
                contextTokenized = self.tokenize(context.lower())
                contextLength = len(contextTokenized)
                if contextLength > maxLenContext:
                    maxLenContext = contextLength

                question = data['query']
                question = question.replace("''", '" ')
                question = question.replace("``", '" ')
                questionTokenized = self.tokenize(question.lower())
                if len(questionTokenized) > maxLenQuestion:
                    maxLenQuestion = len(questionTokenized)

                question_id = data['query_id']

                answerFound = False

                for answer in data['answers']:
                    answerTokenized = self.tokenize(answer.lower())
                    if len(answerTokenized) == 0:
                        continue
                    answerBeginIndex, answerEndIndex = self.findAnswer(contextTokenized, answerTokenized)
                    if answerBeginIndex != None:
                        xContext.append(contextTokenized)
                        xQuestion.append(questionTokenized)
                        xQuestionID.append(question_id)
                        xAnswerBegin.append(answerBeginIndex)
                        xAnswerEnd.append(answerEndIndex)
                        xAnswerText.append(answer)
                        xLen.append(len(contextTokenized))
                        qLen.append(len(questionTokenized))
                        answerFound = True
                        break

                if answerFound:
                    answerFound = False
                    break


        return xContext, xLen, xQuestion, qLen, xQuestionID, xAnswerBegin, xAnswerEnd, xAnswerText, maxLenContext, maxLenQuestion


    def splitMsmarcoDatasetsValMulti(self, f):
        '''Given a parsed Json data object, split the object into training context (paragraph), question, answer matrices,
           and keep track of max context and question lengths.
        '''
        xContext = [] # list of list of contexts paragraphs
        xmLen = [] # list of list of unpadded lengths
        maxLenContext = 0

        for data in f['data']:
            passages = []
            x_len = []

            answerFound = False
            for passage in data['passages']:
                context = passage['passage_text']
                contextTokenized = self.tokenize(context.lower())

                passages.append(contextTokenized)
                x_len.append(len(contextTokenized))

                contextLength = len(contextTokenized)
                if contextLength > maxLenContext:
                    maxLenContext = contextLength

                # Only worry about when the answer is verbatim in the text
                if not answerFound and passage['is_selected'] == 1:
                    for answer in data['answers']:
                        answerTokenized = self.tokenize(answer.lower())
                        answerBeginIndex, answerEndIndex = self.findAnswer(contextTokenized, answerTokenized)
                        if answerBeginIndex != None:
                            answerFound = True
                            break

            if answerFound:
                xContext.append(passages)
                xmLen.append(x_len)

        return xContext, xmLen, maxLenContext

    def splitMsmarcoDatasetsTest(self, f):
        '''Given a parsed Json data object, split the object into training context (paragraph), question, answer matrices,
           and keep track of max context and question lengths.
        '''
        xContext = [] # list of list of contexts paragraphs
        xQuestion = [] # list of questions
        xQuestionID = [] # list of question id
        xUrl = [] # list of list of urls associated with the passages
        xLen = []
        qLen = []
        maxLenContext = 0
        maxLenQuestion = 0

        for data in f['data']:
            passages = []
            x_len = []
            urls = []

            for passage in data['passages']:
                context = passage['passage_text']
                contextTokenized = self.tokenize(context.lower())

                passages.append(contextTokenized)
                x_len.append(len(contextTokenized))
                urls.append(passage['url'])

                contextLength = len(contextTokenized)
                if contextLength > maxLenContext:
                    maxLenContext = contextLength

            question = data['query']
            questionTokenized = self.tokenize(question.lower())
            if len(questionTokenized) > maxLenQuestion:
                maxLenQuestion = len(questionTokenized)

            questionID = data['query_id']

            xContext.append(passages)
            xQuestion.append(questionTokenized)
            xQuestionID.append(questionID)
            xUrl.append(urls)
            xLen.append(x_len)
            qLen.append(len(questionTokenized))

        return xContext, xLen, xQuestion, qLen, xQuestionID, xUrl, maxLenContext, maxLenQuestion


    def findAnswer(self, contextTokenized, answerTokenized):
        contextLen = len(contextTokenized)
        answerLen = len(answerTokenized)
        for i in range(contextLen - answerLen + 1):
            match = sum([1 for j, m in zip(contextTokenized[i:i + answerLen], answerTokenized) if j == m])
            if match == answerLen:
                return (i, i + answerLen - 1)
        return (None, None)

    def vectorizeData(self, xContext, xQuestion, xAnswerBegin, xAnswerEnd, word_index, context_maxlen, question_maxlen):
        '''Converts context and question words to their respective index and pad context to max context length
           and question to max question length. *Convert answers to one-hot vectors of length max context.
        '''
        X = []
        Xq = []
        YBegin = []
        YEnd = []
        smart_unk_counts = [0 for _ in range(len(self.unknown_classes))]
        for i in range(len(xContext)):
            x = []
            for w in xContext[i]:
                if w in word_index:
                    x.append(word_index[w])
                else:
                    for j in range(len(self.unknown_classes)):
                        if re.match(self.unknown_classes[j], w):
                            x.append(len(word_index) + j)
                            smart_unk_counts[j] += 1
                            break
            xq = []
            for w in xQuestion[i]:
                if w in word_index:
                    xq.append(word_index[w])
                else:
                    for j in range(len(self.unknown_classes)):
                        if re.match(self.unknown_classes[j], w):
                            x.append(len(word_index) + j)
                            break
            # map the first and last words of answer span to one-hot representations
            y_Begin =  xAnswerBegin[i]
            y_End = xAnswerEnd[i]
            X.append(x)
            Xq.append(xq)
            YBegin.append(y_Begin)
            YEnd.append(y_End)

        print('Smart Unk Counts:', smart_unk_counts)
        print('Percentage Unknown:', sum(smart_unk_counts) / sum(len(p) for p in xContext))
        return self.pad_sequences(X, context_maxlen), self.pad_sequences(Xq, question_maxlen), YBegin, YEnd

    def vectorizeDataMutli(self, xContext, xQuestion, word_index, context_maxlen, question_maxlen):
        '''Converts context and question words to their respective index and pad context to max context length
           and question to max question length. *Convert answers to one-hot vectors of length max context.
        '''
        X = []
        Xq = []

        smart_unk_counts = [0 for _ in range(len(self.unknown_classes))]
        for i in range(len(xContext)):
            xs = []
            for p in xContext[i]:
                x = []
                for w in p:
                    if w in word_index:
                        x.append(word_index[w])
                    else:
                        for j in range(len(self.unknown_classes)):
                            if re.match(self.unknown_classes[j], w):
                                x.append(len(word_index) + j)
                                smart_unk_counts[j] += 1
                                break
                xs.append(x)
            xq = []
            for w in xQuestion[i]:
                if w in word_index:
                    xq.append(word_index[w])
                else:
                    for j in range(len(self.unknown_classes)):
                        if re.match(self.unknown_classes[j], w):
                            x.append(len(word_index) + j)
                            break

            X.append(self.pad_sequences(xs, context_maxlen))
            Xq.append(xq)
        
        print('Smart Unk Counts:', smart_unk_counts)
        print('Percentage Unknown:', sum(smart_unk_counts) / sum(len(p) for s in xContext for p in s))
        return X, self.pad_sequences(Xq, question_maxlen)

    def pad_sequences(self, X, maxlen):
        '''Pad with self.vocab_size + len(self.unknown_classes) which is reserved for the padding vector'''
        for context in X:
            for i in range(maxlen - len(context)):
                context.append(self.vocab_size + len(self.unknown_classes))
        return X

    def passageRevelevance(self, xContext, xQuestion):
        cs = []
        for i in tqdm(range(len(xContext))):
            passages = [' '.join(p) for p in xContext[i]]

            tfidf = TfidfVectorizer(stop_words='english', binary=True).fit_transform([' '.join(xQuestion[i])] + passages)
            cosine_similarities = linear_kernel(tfidf[0:1], tfidf).flatten()[1:]

            # Normalize
            sum_cs = sum(cosine_similarities)
            if sum_cs > 0:
                cosine_similarities = [x / sum_cs for x in cosine_similarities]
            else:
                cosine_similarities = [1.0 / len(passages) for _ in range(len(passages))]

            cs.append(cosine_similarities)

        return cs

    def saveAnswersForEvalVal(self, questionType, modelName, vContextPred, vQuestionID, predictedBegin, predictedEnd):
        ref_fn = './references/' + questionType + '.json'
        can_fn = './candidates/' + questionType + '_' + modelName + '.json'

        rf = open(ref_fn, 'w', encoding='utf-8')
        cf = open(can_fn, 'w', encoding='utf-8')

        answers = {}
        with open('./datasets/msmarco/dev/' + questionType + '.json', encoding='utf-8') as f:
            for line in f:
                sample = json.loads(line)
                if len(sample['answers']) > 0:
                    answers[sample['query_id']] = sample['answers'][0].lower()
                else:
                    answers[sample['query_id']] = ''

        for i in range(len(vContextPred)):
            predictedAnswer = ' '.join(vContextPred[i][predictedBegin[i] : predictedEnd[i] + 1])

            reference = {}
            candidate = {}
            reference['query_id'] = vQuestionID[i]
            reference['answers'] = [answers[vQuestionID[i]]]

            candidate['query_id'] = vQuestionID[i]
            candidate['answers'] = [predictedAnswer]

            print(json.dumps(reference, ensure_ascii=False), file=rf)
            print(json.dumps(candidate, ensure_ascii=False), file=cf)

        rf.close()
        cf.close()

    def saveAnswersForEvalTestDemo(self, questionType, candidateName, teContext, teQuestionID, teUrl, predictedBegin, predictedEnd, passageWeights, logitsStart, logitsEnd, tePassageIndex):
        ANSWER_DIR = ('../cse481n-blog/demo/data/answers/{}'
                      .format(self.config.model))

        if not os.path.exists(ANSWER_DIR):
            os.makedirs(ANSWER_DIR)

        for query_index in range(len(teContext)):
            query_id = teQuestionID[query_index]

            def get_passage(passage_index):
                return {
                    'tokens': teContext[query_index][passage_index],
                    'relevance': passageWeights[query_index][passage_index],
                    'logits_start': logitsStart[query_index][passage_index],
                    'logits_end': logitsEnd[query_index][passage_index],
                    'url': teUrl[query_index][passage_index],
                }

            with open('{}/{}.json'.format(ANSWER_DIR, query_id), 'w+') as out:
                passages = [get_passage(i)
                            for i in range(len(teContext[query_index]))]

                selected_passage = passages[tePassageIndex[query_index]]
                selected_passage['selected'] = True
                selected_passage['start_index'] = predictedBegin[query_index]
                selected_passage['end_index'] = predictedEnd[query_index]

                passages.sort(key=lambda p: p['relevance'], reverse=True)

                candidate = {
                    'query_id': query_id,
                    'passages': passages,
                }

                json.dump(candidate, out, ensure_ascii=False)

    def importMsmarco(self, json_file):
        data = {}
        data['data'] = []
        with open(json_file, encoding='utf-8') as f:
            data['data'] = [json.loads(line) for line in f]
        return data

    def buildVocab(self, sentences):
        '''Accepts a list of list of words. For example, a list of contexts or questions that are tokenized.
           Returns a sorted list of strings that comprise the vocabulary.
        '''
        vocab = set([word for words in sentences for word in words])
        vocab = sorted(vocab)
        return vocab
