"use client";

import { useEffect, useState } from "react";

type SourceKey = "local" | "s3" | "azure" | "sharepoint";

const sources: Record<
  SourceKey,
  {
    label: string;
    eyebrow: string;
    tone: string;
    summary: string;
    contract: string;
    auth: string;
    route: string;
    steps: string[];
    checks: string[];
  }
> = {
  local: {
    label: "ローカル",
    eyebrow: "NOW / VERIFIED",
    tone: "mint",
    summary: "契約なしで始める基準経路。CLIでmanaged Volumeへ送り、共通ETLを先に固めます。",
    contract: "不要",
    auth: "Databricks OAuth U2M",
    route: "CLI → UC Volume → read_files → Delta",
    steps: [
      "検証用CSV・ZIPを用意する",
      "Databricks CLIでBronze Volumeへアップロードする",
      "read_filesで型推論し、明示的にcastする",
      "source_uri・取得日時などの監査列を付ける",
      "Silver Deltaテーブルを再作成し件数を照合する",
    ],
    checks: ["255行を取り込み済み", "source_uri欠損 0件", "2回実行後も255行"],
  },
  s3: {
    label: "Amazon S3",
    eyebrow: "NEXT / SIMPLEST",
    tone: "amber",
    summary: "AWS Workspaceと同じクラウド。外部ストレージ検証の第一候補です。",
    contract: "AWSアカウントとS3 bucket",
    auth: "IAM role（Access Keyは使わない）",
    route: "S3 → External Location → Auto Loader → Delta",
    steps: [
      "us-west-2に非公開の検証用bucketを作る",
      "Versioningとライフサイクルを設定する",
      "Databricksの自動セットアップでIAM連携する",
      "読み取り専用External Locationを作る",
      "Auto Loaderで追加・更新ファイルを差分取得する",
    ],
    checks: ["公開アクセス禁止", "同一ファイルの重複なし", "ETagと更新日時を追跡"],
  },
  azure: {
    label: "Azure Storage",
    eyebrow: "CROSS CLOUD",
    tone: "blue",
    summary: "AWS DatabricksからADLS Gen2へ接続し、クロスクラウド転送も評価します。",
    contract: "Azure subscriptionとADLS Gen2",
    auth: "Entra ID Service Principal",
    route: "ADLS Gen2 → abfss → Auto Loader / COPY INTO",
    steps: [
      "Hierarchical namespace有効のStorage Accountを作る",
      "検証用containerと限定prefixを作る",
      "読み取り専用Service Principalを用意する",
      "Client SecretをDatabricks Secretへ登録する",
      "abfss接続後、差分と転送コストを測る",
    ],
    checks: ["Secretをコードに置かない", "DBFS mountを使わない", "転送費用と遅延を記録"],
  },
  sharepoint: {
    label: "SharePoint",
    eyebrow: "BETA CONNECTOR",
    tone: "violet",
    summary: "Lakeflow ConnectでCSV・Excel・PDFを継続同期し、文書系データも扱います。",
    contract: "Microsoft 365 / SharePoint Online",
    auth: "Databricks-managed OAuth U2M",
    route: "SharePoint → Lakeflow Connect → Streaming table → Silver",
    steps: [
      "検証用siteとdocument libraryを作る",
      "WorkspaceでSharePoint connector Previewを有効化する",
      "Microsoft 365ユーザーでOAuth接続する",
      "CSV・Excel・PDFのsource tableを作る",
      "更新・削除・文書本文抽出を検証する",
    ],
    checks: ["対象folderだけ許可", "元URLを追跡", "本番前にBeta制約を再評価"],
  },
};

const phases = [
  ["01", "収集", "API・ファイルを改変せず保存"],
  ["02", "Bronze", "原本・hash・取得台帳"],
  ["03", "Silver", "正規化・型変換・品質検査"],
  ["04", "Gold", "企業比較用の標準指標"],
  ["05", "Genie", "AgentをGenie Oneから利用"],
];

const checklist = [
  "機密情報を含まない検証データを用意",
  "接続は読み取り専用・限定prefixから開始",
  "初回・追加・更新・削除の4ケースを実施",
  "再実行後の論理行数を確認",
  "Silver行から原本URIへ逆引き",
  "費用・時間・運用負荷を記録",
];

export default function Manual() {
  const [active, setActive] = useState<SourceKey>("local");
  const [done, setDone] = useState<boolean[]>(checklist.map(() => false));

  useEffect(() => {
    const stored = window.localStorage.getItem("edinet-poc-checklist");
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length === checklist.length) setDone(parsed);
      } catch {
        // Ignore malformed device-local state.
      }
    }
  }, []);

  const toggle = (index: number) => {
    const next = done.map((value, item) => (item === index ? !value : value));
    setDone(next);
    window.localStorage.setItem("edinet-poc-checklist", JSON.stringify(next));
  };

  const current = sources[active];
  const complete = done.filter(Boolean).length;

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="EDINET Lakehouse Manual トップ">
          <span className="brand-mark">E</span>
          <span>EDINET LAKEHOUSE MANUAL</span>
        </a>
        <nav aria-label="ページ内ナビゲーション">
          <a href="#architecture">全体像</a>
          <a href="#sources">接続手順</a>
          <a href="#checklist">チェック</a>
          <a href="https://github.com/souk530/edinet-test">GitHub</a>
        </nav>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="kicker">DATA ENGINEERING PLAYBOOK · 2026</p>
          <h1>
            点在するデータを、
            <span>説明できるSilver</span>へ。
          </h1>
          <p className="lead">
            EDINET、ローカル、S3、Azure、SharePointをDatabricksへ集約し、
            原本からGenie Oneの回答まで追跡できるデータ基盤をつくるための図解マニュアルです。
          </p>
          <div className="hero-actions">
            <a className="button primary" href="#architecture">全体像から読む</a>
            <a className="button ghost" href="#sources">ソースを選ぶ</a>
          </div>
        </div>
        <aside className="status-card" aria-label="現在の検証状況">
          <div className="status-head">
            <span>POC STATUS</span>
            <span className="live-dot">ACTIVE</span>
          </div>
          <div className="metric"><strong>255</strong><span>Silver rows</span></div>
          <div className="metric-row">
            <div><strong>1</strong><span>document</span></div>
            <div><strong>0</strong><span>missing URI</span></div>
            <div><strong>2×</strong><span>idempotent</span></div>
          </div>
          <p>Local → UC Volume → Silver は検証済み</p>
        </aside>
      </section>

      <section className="section architecture" id="architecture">
        <div className="section-heading">
          <p className="section-no">01 / ARCHITECTURE</p>
          <h2>データは段階的に、意味を増やす</h2>
          <p>原本を残したまま、技術的な正規化と業務上の指標統合を分離します。</p>
        </div>
        <div className="source-clouds" aria-label="入力データソース">
          {Object.entries(sources).map(([key, source]) => (
            <button key={key} className={`source-chip ${source.tone}`} onClick={() => setActive(key as SourceKey)}>
              <span>{source.eyebrow}</span>{source.label}
            </button>
          ))}
          <div className="flow-arrow" aria-hidden="true">↓</div>
        </div>
        <div className="pipeline" aria-label="Lakehouseの処理段階">
          {phases.map(([number, title, note], index) => (
            <div className="phase-wrap" key={number}>
              <article className={`phase phase-${index + 1}`}>
                <span>{number}</span>
                <h3>{title}</h3>
                <p>{note}</p>
              </article>
              {index < phases.length - 1 && <div className="connector" aria-hidden="true">→</div>}
            </div>
          ))}
        </div>
        <div className="rule-strip">
          <span>原本は変更しない</span><span>欠損と0を分ける</span><span>再実行で増やさない</span><span>回答から原本へ戻れる</span>
        </div>
      </section>

      <section className="section source-guide" id="sources">
        <div className="section-heading split">
          <div>
            <p className="section-no">02 / SOURCE PLAYBOOK</p>
            <h2>接続先ごとの進め方</h2>
          </div>
          <p>最初はローカルでETLを固め、外部サービスでは接続と差分取得だけを追加検証します。</p>
        </div>

        <div className="tabs" role="tablist" aria-label="データソース選択">
          {(Object.keys(sources) as SourceKey[]).map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={active === key}
              className={active === key ? "active" : ""}
              onClick={() => setActive(key)}
            >
              {sources[key].label}
            </button>
          ))}
        </div>

        <article className={`playbook ${current.tone}`}>
          <div className="playbook-summary">
            <p className="eyebrow">{current.eyebrow}</p>
            <h3>{current.label}</h3>
            <p>{current.summary}</p>
            <dl>
              <div><dt>必要契約</dt><dd>{current.contract}</dd></div>
              <div><dt>認証</dt><dd>{current.auth}</dd></div>
              <div><dt>データ経路</dt><dd>{current.route}</dd></div>
            </dl>
          </div>
          <div className="steps">
            <p className="eyebrow">STEP BY STEP</p>
            <ol>
              {current.steps.map((step, index) => (
                <li key={step}><span>{String(index + 1).padStart(2, "0")}</span><p>{step}</p></li>
              ))}
            </ol>
          </div>
          <div className="acceptance">
            <p className="eyebrow">ACCEPTANCE</p>
            {current.checks.map((check) => <p key={check}><span>✓</span>{check}</p>)}
          </div>
        </article>
      </section>

      <section className="section silver" id="silver">
        <div className="section-heading">
          <p className="section-no">03 / SILVER CONTRACT</p>
          <h2>どこから来ても、同じ監査列を持つ</h2>
        </div>
        <div className="schema-board">
          <div className="schema-title">
            <span>DELTA TABLE</span>
            <strong>workspace.edinet_silver.*</strong>
          </div>
          <div className="schema-grid">
            {["source_system", "source_uri", "source_file_name", "source_modified_at", "source_size", "source_etag", "source_sha256", "ingested_at", "pipeline_run_id", "schema_version", "record_status", "rescued_data"].map((field) => (
              <code key={field}>{field}</code>
            ))}
          </div>
        </div>
        <div className="principles">
          <article><b>01</b><h3>Traceable</h3><p>Silverの1行から原本ファイルと実行履歴へ戻れる。</p></article>
          <article><b>02</b><h3>Idempotent</h3><p>同じ入力を何度処理しても論理行数が増えない。</p></article>
          <article><b>03</b><h3>Observable</h3><p>変換不能値を捨てず、rescued dataとDQ結果に残す。</p></article>
        </div>
      </section>

      <section className="section checklist" id="checklist">
        <div className="checklist-copy">
          <p className="section-no">04 / RUN CHECKLIST</p>
          <h2>PoCを「接続できた」で終わらせない</h2>
          <p>チェック状態はこの端末のブラウザだけに保存されます。</p>
          <div className="progress" aria-label={`${complete}/${checklist.length} 完了`}>
            <div style={{ width: `${(complete / checklist.length) * 100}%` }} />
          </div>
          <strong>{complete} / {checklist.length} completed</strong>
        </div>
        <div className="check-items">
          {checklist.map((item, index) => (
            <label key={item} className={done[index] ? "checked" : ""}>
              <input type="checkbox" checked={done[index]} onChange={() => toggle(index)} />
              <span className="box" aria-hidden="true">{done[index] ? "✓" : ""}</span>
              <span>{item}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="section next-step">
        <p className="section-no">NEXT MOVE</p>
        <h2>いまは契約せず、共通ETLを完成させる。</h2>
        <p>次にS3を契約した時点で、External LocationとAuto Loaderの検証を追加します。</p>
        <a className="button primary" href="https://github.com/souk530/edinet-test/tree/main/docs">詳細Markdownを読む</a>
      </section>

      <footer>
        <span>EDINET LAKEHOUSE POC</span>
        <span>Bronze → Silver → Gold → Genie One</span>
        <span>Updated 2026-08-16</span>
      </footer>
    </main>
  );
}
