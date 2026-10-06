"use strict";

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PronounceBrowserStore = api;
})(globalThis, function () {
  const DEFAULT_DATABASE_NAME = "pronunciation-lab";
  const DATABASE_VERSION = 1;
  const STORES = {
    articles: "id",
    sessions: "id",
    attempts: ["session_id", "id"],
    jobs: ["session_id", "attempt_id", "id"],
    views: ["session_id", "attempt_id", "job_id"],
    summaries: "session_id",
    coaching: "session_id",
    readingFeedback: "session_id",
    events: { keyPath: "sequence", autoIncrement: true },
    practice: "id",
    cache: "session_id",
    ledger: { keyPath: "sequence", autoIncrement: true },
    transitions: { keyPath: "sequence", autoIncrement: true },
  };

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = resolve;
      transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
    });
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
    });
  }

  function notFound(key) {
    const error = new Error(`No browser record exists for ${JSON.stringify(key)}`);
    error.name = "RecordNotFoundError";
    return error;
  }

  function required(value, key) {
    if (value === undefined) throw notFound(key);
    return value;
  }

  function openDatabase(name) {
    if (typeof indexedDB === "undefined") {
      return Promise.reject(new Error("IndexedDB is not available in this browser context"));
    }
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(name, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        for (const [storeName, definition] of Object.entries(STORES)) {
          if (!database.objectStoreNames.contains(storeName)) {
            const options = typeof definition === "string" || Array.isArray(definition)
              ? { keyPath: definition }
              : definition;
            const store = database.createObjectStore(storeName, options);
            if (storeName === "events") store.createIndex("session_id", "session_id");
          }
        }
      };
      request.onerror = () => reject(request.error || new Error("Could not open the IndexedDB database"));
      request.onblocked = () => reject(new Error("Opening the IndexedDB database was blocked"));
      request.onsuccess = () => resolve(request.result);
    });
  }

  class BrowserStore {
    constructor(name, database) {
      this.name = name;
      this.database = database;
      this.closed = false;
      this._longitudinalStore = null;
    }

    static async open(name = DEFAULT_DATABASE_NAME) {
      if (typeof name !== "string" || !name.trim()) throw new TypeError("Database name must be a non-empty string");
      return new BrowserStore(name, await openDatabase(name));
    }

    close() {
      if (!this.closed) {
        this.database.close();
        this.closed = true;
      }
    }

    _transaction(storeNames, mode, action) {
      if (this.closed) throw new Error("BrowserStore is closed");
      const transaction = this.database.transaction(storeNames, mode);
      const done = transactionDone(transaction);
      try {
        action(transaction);
      } catch (error) {
        transaction.abort();
        return done.catch(() => { throw error; });
      }
      return done;
    }

    async _read(storeName, action) {
      if (this.closed) throw new Error("BrowserStore is closed");
      const transaction = this.database.transaction(storeName, "readonly");
      const done = transactionDone(transaction);
      try {
        const result = await requestResult(action(transaction.objectStore(storeName)));
        await done;
        return result;
      } catch (error) {
        await done.catch(() => {});
        throw error;
      }
    }

    async _put(storeName, value) {
      await this._transaction(storeName, "readwrite", (tx) => tx.objectStore(storeName).put(value));
    }

    async _add(storeName, value) {
      try {
        await this._transaction(storeName, "readwrite", (tx) => tx.objectStore(storeName).add(value));
      } catch (error) {
        if (error && error.name === "ConstraintError") {
          const duplicate = new Error("A browser record with this key already exists");
          duplicate.name = "DuplicateRecordError";
          throw duplicate;
        }
        throw error;
      }
    }

    async _load(storeName, key) {
      return required(await this._read(storeName, (store) => store.get(key)), key);
    }

    async sessionIds() {
      return (await this._read("sessions", (store) => store.getAllKeys())).sort();
    }

    async createSession(session) {
      await this._add("sessions", session);
    }

    async saveSession(session) {
      await this._put("sessions", session);
    }

    async loadSession(sessionId) {
      return this._load("sessions", sessionId);
    }

    async saveArticle(article) {
      await this._add("articles", article);
    }

    async loadArticle(articleId) {
      return this._load("articles", articleId);
    }

    async attemptExists(sessionId, attemptId) {
      return (await this._read("attempts", (store) => store.get([sessionId, attemptId]))) !== undefined;
    }

    async saveAttempt(attempt) {
      await this._put("attempts", attempt);
    }

    async loadAttempt(sessionId, attemptId) {
      return this._load("attempts", [sessionId, attemptId]);
    }

    async saveJob(job) {
      await this._put("jobs", job);
    }

    async loadJob(sessionId, attemptId, jobId) {
      return this._load("jobs", [sessionId, attemptId, jobId]);
    }

    async saveView(job, view) {
      await this._put("views", {
        session_id: job.session_id,
        attempt_id: job.attempt_id,
        job_id: job.id,
        value: view,
      });
    }

    async loadView(job) {
      const row = await this._read("views", (store) => store.get([job.session_id, job.attempt_id, job.id]));
      return row === undefined ? null : row.value;
    }

    async saveSummary(sessionId, summary) {
      await this._put("summaries", { session_id: sessionId, value: summary });
    }

    async loadSummary(sessionId) {
      const row = await this._read("summaries", (store) => store.get(sessionId));
      return row === undefined ? null : row.value;
    }

    async saveCoaching(sessionId, coaching) {
      await this._put("coaching", { session_id: sessionId, value: coaching });
    }

    async loadCoaching(sessionId) {
      const row = await this._read("coaching", (store) => store.get(sessionId));
      return row === undefined ? null : row.value;
    }

    async saveReadingFeedback(sessionId, feedback) {
      await this._put("readingFeedback", { session_id: sessionId, value: feedback });
    }

    async loadReadingFeedback(sessionId) {
      const row = await this._read("readingFeedback", (store) => store.get(sessionId));
      return row === undefined ? null : row.value;
    }

    async appendEvent(sessionId, kind, data = {}) {
      await this._transaction("events", "readwrite", (tx) => {
        tx.objectStore("events").add({ ...data, session_id: sessionId, kind, at: new Date().toISOString() });
      });
    }

    async events(sessionId) {
      const rows = await this._read("events", (store) => store.index("session_id").getAll(sessionId));
      return rows.map(({ sequence, session_id, ...event }) => event);
    }

    progressStore() {
      if (this.closed) throw new Error("BrowserStore is closed");
      if (!this._longitudinalStore) this._longitudinalStore = new BrowserLongitudinalStore(this);
      return this._longitudinalStore;
    }
  }

  class BrowserLongitudinalStore {
    constructor(store) {
      this.store = store;
    }

    async savePractice(record) {
      await this.store._add("practice", record);
    }

    async practiceRecords() {
      return (await this.store._read("practice", (store) => store.getAll()))
        .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    }

    async loadCache() {
      const rows = await this.store._read("cache", (store) => store.getAll());
      if (!rows.length) return {};
      const readings = {};
      for (const row of rows) Object.assign(readings, row.readings);
      return { extractor_version: rows[0].extractor_version, readings };
    }

    async saveCache(cache, sessions = null) {
      if (!cache || !cache.readings || typeof cache.readings !== "object") {
        throw new TypeError("Cache must contain a readings object");
      }
      const bySession = new Map();
      for (const [readingId, entry] of Object.entries(cache.readings)) {
        const sessionId = readingId.split(":", 1)[0];
        if (!bySession.has(sessionId)) bySession.set(sessionId, {});
        bySession.get(sessionId)[readingId] = entry;
      }
      const selected = sessions === null ? null : new Set(sessions);
      await this.store._transaction("cache", "readwrite", (tx) => {
        const objectStore = tx.objectStore("cache");
        const keys = objectStore.getAllKeys();
        keys.onsuccess = () => {
          for (const [sessionId, readings] of bySession) {
            if (selected === null || selected.has(sessionId)) {
              objectStore.put({ session_id: sessionId, extractor_version: cache.extractor_version, readings });
            }
          }
          for (const sessionId of keys.result) {
            if (!bySession.has(sessionId)) objectStore.delete(sessionId);
          }
        };
      });
    }

    async ledger() {
      return (await this.store._read("ledger", (store) => store.getAll())).map((row) => row.value);
    }

    async appendLedger(lines) {
      if (!lines.length) return;
      await this.store._transaction("ledger", "readwrite", (tx) => {
        const objectStore = tx.objectStore("ledger");
        for (const line of lines) objectStore.add({ value: line });
      });
    }

    async transitionsLog() {
      return (await this.store._read("transitions", (store) => store.getAll())).map((row) => row.value);
    }

    async appendTransitions(lines) {
      if (!lines.length) return;
      await this.store._transaction("transitions", "readwrite", (tx) => {
        const objectStore = tx.objectStore("transitions");
        for (const line of lines) objectStore.add({ value: line });
      });
    }
  }

  return { BrowserStore, BrowserLongitudinalStore, DEFAULT_DATABASE_NAME };
});
