// Settings → Fleet backup: export and import of printers and their settings.
import React, { useState } from 'react';
import { downloadFleetBackup, importFleetBackup, type FleetImportReport } from '../../api/settings';
import { Icons } from '../../components/icons';
import { FieldRow, PageHeader } from '../../components/settingsUi';
import { SettingsIcons } from './icons';

export function FleetBackupPage() {
  const [downloading, setDownloading] = useState(false);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);

  const [importing, setImporting] = useState(false);
  const [importReport, setImportReport] = useState<FleetImportReport | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

  async function handleDownload() {
    setDownloading(true);
    setDownloadErr(null);
    try {
      await downloadFleetBackup();
    } catch (e) {
      setDownloadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setImportReport(null);
    setImportErr(null);
    try {
      const report = await importFleetBackup(file);
      setImportReport(report);
    } catch (err) {
      setImportErr(err instanceof Error ? err.message : String(err));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="Fleet backup & restore"
          sub="Export all printer configs (IPs, tokens, Orca profiles, filament slots) to a file. Import on any Themis install to recreate the same fleet."
        />

        {/* Export */}
        <div style={{ marginBottom: 8, fontSize: 11, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
          Export
        </div>
        <FieldRow
          label="Download backup"
          hint="Saves a JSON file with every printer's connection config, Orca profiles, and filament slot setup. Tokens and IPs are included — keep the file secure."
        >
          <div className="col gap-2">
            <button className="btn primary sm" disabled={downloading} onClick={handleDownload} style={{ width: 'fit-content' }}>
              {React.cloneElement(SettingsIcons.backup, { size: 14 } as React.SVGProps<SVGSVGElement>)}
              {downloading ? 'Exporting…' : 'Download backup'}
            </button>
            {downloadErr && (
              <span className="small text-err">{downloadErr}</span>
            )}
          </div>
        </FieldRow>

        {/* Import */}
        <div style={{ marginTop: 24, marginBottom: 8, fontSize: 11, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
          Restore
        </div>
        <FieldRow
          label="Import backup"
          hint="Printers in the file are added to this install. Existing printers are not removed. Orca profile names are matched against the current catalog — mismatches are reported but don't block the import."
        >
          <div className="col gap-2">
            <div className="row gap-2">
              <button
                className="btn sm"
                disabled={importing}
                onClick={() => fileRef.current?.click()}
                style={{ width: 'fit-content' }}
              >
                {importing ? 'Importing…' : <>{Icons.upload} Choose file & import</>}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".json,application/json"
                style={{ display: 'none' }}
                onChange={handleFileChange}
              />
            </div>
            {importErr && (
              <span className="small text-err">{importErr}</span>
            )}
          </div>
        </FieldRow>

        {/* Post-import report */}
        {importReport && (
          <div style={{
            marginTop: 20,
            padding: '16px 20px',
            background: 'var(--bg-0)',
            border: `1px solid ${importReport.skipped > 0 || importReport.warnings.length > 0 ? 'var(--warn)' : 'var(--ok)'}`,
            borderRadius: 10,
          }}>
            <div className="row gap-3" style={{ marginBottom: importReport.warnings.length > 0 ? 12 : 0 }}>
              <div className="grow">
                <span className="small" style={{ fontWeight: 600, color: 'var(--text-1)' }}>
                  Import complete
                </span>
                <span className="small muted" style={{ marginLeft: 10 }}>
                  {importReport.imported} printer{importReport.imported !== 1 ? 's' : ''} added
                  {importReport.skipped > 0 ? `, ${importReport.skipped} skipped` : ''}
                </span>
              </div>
            </div>
            {importReport.warnings.length > 0 && (
              <div className="col gap-1">
                <div className="tiny" style={{ color: 'var(--warn)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
                  Profile warnings ({importReport.warnings.length})
                </div>
                {importReport.warnings.map((w, i) => (
                  <div key={i} className="tiny muted" style={{ padding: '4px 8px', background: 'var(--bg-1)', borderRadius: 6, lineHeight: 1.5 }}>
                    {w}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// =========================================================================
// Webhook page
// =========================================================================
