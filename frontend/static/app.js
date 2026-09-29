const STORAGE_KEY = "hackathon-platform-state-v1";

const defaultState = {
  users: [
    { id: 1, name: "Ava Organizer", email: "ava@hack.io", role: "organizer" },
    { id: 2, name: "Liam Judge", email: "liam@hack.io", role: "judge" },
  ],
  events: [
    {
      id: 3,
      name: "HackFest 2026",
      date: "2026-11-20",
      location: "Bangalore",
      description: "48-hour product hackathon for student builders.",
    },
  ],
  projects: [
    { id: 4, teamName: "CodeStorm", title: "MediAssist AI", track: "Health" },
  ],
  scores: [],
};

function cloneDefaultState() {
  return JSON.parse(JSON.stringify(defaultState));
}

function loadState() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return cloneDefaultState();

  try {
    const parsed = JSON.parse(raw);
    return {
      users: Array.isArray(parsed.users) ? parsed.users : cloneDefaultState().users,
      events: Array.isArray(parsed.events) ? parsed.events : cloneDefaultState().events,
      projects: Array.isArray(parsed.projects)
        ? parsed.projects
        : cloneDefaultState().projects,
      scores: Array.isArray(parsed.scores) ? parsed.scores : [],
    };
  } catch {
    return cloneDefaultState();
  }
}

const state = loadState();

function persistState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

let idCounter = Math.max(
  0,
  ...state.users.map((item) => Number(item.id) || 0),
  ...state.events.map((item) => Number(item.id) || 0),
  ...state.projects.map((item) => Number(item.id) || 0),
  ...state.scores.map((item) => Number(item.id) || 0)
);

function nextId() {
  idCounter += 1;
  return idCounter;
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function avgProjectScore(projectId) {
  const projectScores = state.scores.filter((score) => score.projectId === projectId);
  if (projectScores.length === 0) return "N/A";

  const total = projectScores.reduce((sum, current) => sum + current.total, 0);
  return (total / projectScores.length).toFixed(2);
}

function renderStats() {
  const statsRoot = document.getElementById("stats");
  if (!statsRoot) return;

  const uniqueJudges = new Set(state.scores.map((score) => score.judgeName));
  const blocks = [
    ["Users", state.users.length],
    ["Events", state.events.length],
    ["Projects", state.projects.length],
    ["Judges Active", uniqueJudges.size],
  ];

  statsRoot.innerHTML = blocks
    .map(
      ([label, value]) => `
      <div class="stat">
        <div>${esc(label)}</div>
        <div class="stat-value">${esc(value)}</div>
      </div>`
    )
    .join("");
}

function renderUsers() {
  const table = document.getElementById("users-table");
  if (!table) return;

  table.innerHTML = state.users
    .map(
      (user) => `
      <tr>
        <td>${esc(user.name)}</td>
        <td>${esc(user.email)}</td>
        <td>${esc(user.role)}</td>
        <td><button type="button" data-user-delete="${esc(user.id)}">Remove</button></td>
      </tr>`
    )
    .join("");
}

function renderEvents() {
  const list = document.getElementById("events-list");
  if (!list) return;

  list.innerHTML = state.events
    .map(
      (event) => `
      <article class="list-item">
        <strong>${esc(event.name)}</strong><br />
        ${esc(event.date)} • ${esc(event.location)}
        <p>${esc(event.description)}</p>
      </article>`
    )
    .join("");
}

function renderProjects() {
  const table = document.getElementById("projects-table");
  if (table) {
    table.innerHTML = state.projects
      .map(
        (project) => `
      <tr>
        <td>${esc(project.teamName)}</td>
        <td>${esc(project.title)}</td>
        <td>${esc(project.track)}</td>
        <td>${esc(avgProjectScore(project.id))}</td>
      </tr>`
      )
      .join("");
  }

  const scoreSelect = document.getElementById("score-project");
  if (scoreSelect) {
    scoreSelect.innerHTML =
      '<option value="">Select project</option>' +
      state.projects
        .map((project) => `<option value="${esc(project.id)}">${esc(project.title)}</option>`)
        .join("");
  }
}

function renderScores() {
  const table = document.getElementById("scores-table");
  if (!table) return;

  table.innerHTML = state.scores
    .map((score) => {
      const project = state.projects.find((item) => item.id === score.projectId);
      return `
      <tr>
        <td>${esc(project ? project.title : "Unknown project")}</td>
        <td>${esc(score.judgeName)}</td>
        <td>${esc(score.innovation)}</td>
        <td>${esc(score.execution)}</td>
        <td>${esc(score.impact)}</td>
        <td>${esc(score.total)}</td>
      </tr>`;
    })
    .join("");
}

function renderAll() {
  renderStats();
  renderUsers();
  renderEvents();
  renderProjects();
  renderScores();
}

function handleRoleForm() {
  const form = document.getElementById("role-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const name = document.getElementById("user-name").value.trim();
    const email = document.getElementById("user-email").value.trim();
    const role = document.getElementById("user-role").value;
    if (!name || !email || !role) return;

    state.users.push({ id: nextId(), name, email, role });
    persistState();
    form.reset();
    renderAll();
  });

  const usersTable = document.getElementById("users-table");
  if (!usersTable) return;

  usersTable.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-user-delete]");
    if (!button) return;

    const userId = Number(button.dataset.userDelete);
    const before = state.users.length;
    state.users = state.users.filter((user) => user.id !== userId);
    if (state.users.length !== before) {
      persistState();
      renderAll();
    }
  });
}

function handleEventForm() {
  const form = document.getElementById("event-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const name = document.getElementById("event-name").value.trim();
    const date = document.getElementById("event-date").value;
    const location = document.getElementById("event-location").value.trim();
    const description = document.getElementById("event-description").value.trim();
    if (!name || !date || !location || !description) return;

    state.events.push({ id: nextId(), name, date, location, description });
    persistState();
    form.reset();
    renderAll();
  });
}

function handleProjectForm() {
  const form = document.getElementById("project-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const teamName = document.getElementById("team-name").value.trim();
    const title = document.getElementById("project-title").value.trim();
    const track = document.getElementById("project-track").value.trim();
    if (!teamName || !title || !track) return;

    state.projects.push({ id: nextId(), teamName, title, track });
    persistState();
    form.reset();
    renderAll();
  });
}

function handleScoreForm() {
  const form = document.getElementById("score-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const projectId = Number(document.getElementById("score-project").value);
    const judgeName = document.getElementById("judge-name").value.trim();
    const innovation = Number(document.getElementById("innovation-score").value);
    const execution = Number(document.getElementById("execution-score").value);
    const impact = Number(document.getElementById("impact-score").value);
    if (!projectId || !judgeName || !innovation || !execution || !impact) return;

    const total = innovation + execution + impact;
    state.scores.push({
      id: nextId(),
      projectId,
      judgeName,
      innovation,
      execution,
      impact,
      total,
    });

    persistState();
    form.reset();
    renderAll();
  });
}

function init() {
  handleRoleForm();
  handleEventForm();
  handleProjectForm();
  handleScoreForm();
  renderAll();
}

init();
