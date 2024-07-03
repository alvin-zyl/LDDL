import pickle, transformers, tqdm

code, id, comment = pickle.load(open("/dataset/codebert/extracted_raw.pkl", "rb"))

tokenizer = transformers.BertTokenizerFast("lddl/dask/bert/vocab")
training_corpus = (
    code[i : i + 10000]
    for i in range(0, len(code), 10000)
)
bert_tokenizer = tokenizer.train_new_from_iterator(text_iterator=training_corpus, vocab_size=40000)
bert_tokenizer.save_pretrained("codebert_tokenizer")