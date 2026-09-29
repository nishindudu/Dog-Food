const state = {
  users: [
    { id: 1, name: "Ava Organizer", email: "ava@hack.io", role: "organizer" },
    { id: 2, name: "Liam Judge", email: "liam@hack.io", role: "judge" },
  ],
  events: [
    {
      id: 1,
      name: "HackFest 2026",
      date: "2026-11-20",
      location: "Bangalore",
      description: "48-hour product hackathon for student builders.",
    },
  ],
  projects: [
    { id: 1, teamName: "CodeStorm", title: "MediAssist AI", track: "Health" },
  ],
  scores: [],
};

let idCounter = 2;

function nextId() {
  idCounter += 1;
  return idCounter;
}

function avgProjectScore(projectId) {
  const projectScores = state.scores.filter((score) => score.projectId === projectId);
  if (projectScores.length === 0) return "N/A";

  const total = projectScores.reduce((sum, current) => sum + current.total, 0);
  return (total / projectScores.length).toFixed(2);
}

function renderStats() {
  const statsRoot = document.getElementById("stats");
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
        <div>${label}</div>
        <div class="stat-value">${value}</div>
      </div>`
    )
    .join("");
}

function renderUsers() {
  const table = document.getElementById("users-table");
  table.innerHTML = state.users
    .map(
      (user) => `
      <tr>
        <td>${user.name}</td>
        <td>${user.email}</td>
        <td>${user.role}</td>
        <td><button data-user-delete="${user.id}">Remove</button></td>
      </tr>`
    )
    .join("");
}

function renderEvents() {
  const list = document.getElementById("events-list");
  list.innerHTML = state.events
    .map(
      (event) => `
      <article class="list-item">
        <strong>${event.name}</strong><br />
        ${event.date} • ${event.location}
        <p>${event.description}</p>
      </article>`
    )
    .join("");
}

function renderProjects() {
  const table = document.getElementById("projects-table");
  table.innerHTML = state.projects
    .map(
      (project) => `
      <tr>
        <td>${project.teamName}</td>
        <td>${project.title}</td>
        <td>${project.track}</td>
        <td>${avgProjectScore(project.id)}</td>
      </tr>`
    )
    .join("");

  const scoreSelect = document.getElementById("score-project");
  scoreSelect.innerHTML =
    '<option value="">Select project</option>' +
    state.projects
      .map((project) => `<option value="${project.id}">${project.title}</option>`)
      .join("");
}

function renderScores() {
  const table = document.getElementById("scores-table");
  table.innerHTML = state.scores
    .map((score) => {
      const project = state.projects.find((item) => item.id === score.projectId);
      return `
      <tr>
        <td>${project ? project.title : "Unknown project"}</td>
        <td>${score.judgeName}</td>
        <td>${score.innovation}</td>
        <td>${score.execution}</td>
        <td>${score.impact}</td>
        <td>${score.total}</td>
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
  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const name = document.getElementById("user-name").value.trim();
    const email = document.getElementById("user-email").value.trim();
    const role = document.getElementById("user-role").value;

    state.users.push({ id: nextId(), name, email, role });
    form.reset();
    renderAll();
  });

  document.getElementById("users-table").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-user-delete]");
    if (!button) return;

    const userId = Number(button.dataset.userDelete);
    state.users = state.users.filter((user) => user.id !== userId);
    renderAll();
  });
}

function handleEventForm() {
  const form = document.getElementById("event-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const name = document.getElementById("event-name").value.trim();
    const date = document.getElementById("event-date").value;
    const location = document.getElementById("event-location").value.trim();
    const description = document.getElementById("event-description").value.trim();

    state.events.push({ id: nextId(), name, date, location, description });
    form.reset();
    renderAll();
  });
}

function handleProjectForm() {
  const form = document.getElementById("project-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const teamName = document.getElementById("team-name").value.trim();
    const title = document.getElementById("project-title").value.trim();
    const track = document.getElementById("project-track").value.trim();

    state.projects.push({ id: nextId(), teamName, title, track });
    form.reset();
    renderAll();
  });
}

function handleScoreForm() {
  const form = document.getElementById("score-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const projectId = Number(document.getElementById("score-project").value);
    const judgeName = document.getElementById("judge-name").value.trim();
    const innovation = Number(document.getElementById("innovation-score").value);
    const execution = Number(document.getElementById("execution-score").value);
    const impact = Number(document.getElementById("impact-score").value);
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

    form.reset();
    renderAll();
  });
}

handleRoleForm();
handleEventForm();
handleProjectForm();
handleScoreForm();
renderAll();
