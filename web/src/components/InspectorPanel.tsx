"use client";

export default function InspectorPanel() {
  return (
    <div>
      <div className="insp shadow-medium">
        <div className="insp-tabs" id="insp-tabs" />
        <div className="insp-body">
          <div className="insp-left">
            <div className="insp-metricbar" id="insp-metricbar" />
            <div className="insp-trend">
              <div className="panel-label">Best score over iterations</div>
              <svg
                id="trend-svg"
                role="img"
                aria-label="Score trend over evolution iterations"
              />
            </div>
            <div className="insp-tree">
              <div className="panel-label">
                Evolution tree{" "}
                <span className="panel-hint">— click a program</span>
              </div>
              <div className="tree-scroll">
                <svg
                  id="tree-svg"
                  role="group"
                  aria-label="Evolution tree — click a program to view its source"
                />
              </div>
            </div>
          </div>
          <div className="insp-right" id="insp-detail">
            <div className="detail-empty">
              Select a program in the tree to view its source.
            </div>
          </div>
        </div>
      </div>

      <p className="figcap">
        <b>Run record.</b> The two runs with released per-iteration source.
        Click any program to read its full Python; use “Diff vs parent” to see
        exactly what the reflector changed. The memory M★ discovers for
        conversational question answering is structurally unlike the one it
        discovers for embodied planning, although both begin from the same three
        seed programs.
      </p>
    </div>
  );
}
