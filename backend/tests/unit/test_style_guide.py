"""Tests for style guide file discovery and classification."""

from lean_ai.context.style_guide import _classify_file, _discover_style_files


class TestClassifyFile:
    """Test _classify_file category assignment."""

    # Tier 1: CSS framework config
    def test_tailwind_config_js(self):
        assert _classify_file("tailwind.config.js") == "css_framework_config"

    def test_tailwind_config_ts(self):
        assert _classify_file("tailwind.config.ts") == "css_framework_config"

    def test_postcss_config(self):
        assert _classify_file("postcss.config.js") == "css_framework_config"

    def test_webpack_mix(self):
        assert _classify_file("webpack.mix.js") == "css_framework_config"

    # Tier 2: Global CSS
    def test_app_css(self):
        assert _classify_file("resources/css/app.css") == "global_css"

    def test_global_css(self):
        assert _classify_file("src/global.css") == "global_css"

    def test_variables_css(self):
        assert _classify_file("src/styles/variables.css") == "global_css"

    def test_theme_scss(self):
        assert _classify_file("resources/sass/theme.scss") == "global_css"

    def test_main_css(self):
        assert _classify_file("assets/main.css") == "global_css"

    # SCSS partials
    def test_scss_partial_variables(self):
        assert _classify_file("resources/sass/_variables.scss") == "scss_variables"

    def test_scss_partial_colors(self):
        assert _classify_file("resources/sass/_colors.scss") == "scss_variables"

    # Tier 3: Layout templates
    def test_blade_layout_in_layouts_dir(self):
        assert _classify_file("resources/views/layouts/app.blade.php") == "layout_templates"

    def test_blade_master_layout(self):
        assert _classify_file("resources/views/master.blade.php") == "layout_templates"

    def test_vue_app_layout(self):
        assert _classify_file("src/App.vue") == "layout_templates"

    def test_tsx_layout(self):
        assert _classify_file("src/components/Layout.tsx") == "layout_templates"

    def test_html_base(self):
        assert _classify_file("templates/base.html") == "layout_templates"

    # Tier 4: Component templates
    def test_blade_component(self):
        assert _classify_file("resources/views/components/hero.blade.php") == "component_templates"

    def test_blade_partial(self):
        assert _classify_file("resources/views/partials/nav.blade.php") == "component_templates"

    def test_blade_page(self):
        assert _classify_file("resources/views/welcome.blade.php") == "component_templates"

    def test_vue_component(self):
        assert _classify_file("src/components/Header.vue") == "component_templates"

    # Skip compiled output
    def test_skip_public_dir(self):
        assert _classify_file("public/css/app.css") is None

    def test_skip_dist_dir(self):
        assert _classify_file("dist/assets/style.css") is None

    def test_skip_build_dir(self):
        assert _classify_file("build/static/main.css") is None

    def test_skip_node_modules(self):
        assert _classify_file("node_modules/bootstrap/dist/css/bootstrap.css") is None

    # Irrelevant files
    def test_php_model_irrelevant(self):
        assert _classify_file("app/Models/User.php") is None

    def test_python_file_irrelevant(self):
        assert _classify_file("src/utils/helper.py") is None

    def test_json_file_irrelevant(self):
        assert _classify_file("config/app.json") is None


class TestDiscoverStyleFiles:
    """Test _discover_style_files with tmp_path fixtures."""

    def test_laravel_project(self, tmp_path):
        # Create a minimal Laravel-like structure.
        (tmp_path / "resources" / "css").mkdir(parents=True)
        (tmp_path / "resources" / "css" / "app.css").write_text(
            ":root { --primary: #1a1a2e; }"
        )
        (tmp_path / "resources" / "views" / "layouts").mkdir(parents=True)
        (tmp_path / "resources" / "views" / "layouts" / "app.blade.php").write_text(
            "<html>...</html>"
        )
        (tmp_path / "tailwind.config.js").write_text("module.exports = {}")
        # .git dir so list_repo_tree treats it as a repo
        (tmp_path / ".git").mkdir()

        result = _discover_style_files(str(tmp_path))
        assert len(result["css_framework_config"]) >= 1
        assert len(result["global_css"]) >= 1
        assert len(result["layout_templates"]) >= 1

    def test_empty_project(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = _discover_style_files(str(tmp_path))
        total = sum(len(v) for v in result.values())
        assert total == 0

    def test_file_count_limits(self, tmp_path):
        """Categories should be capped at their configured maximums."""
        (tmp_path / ".git").mkdir()
        css_dir = tmp_path / "styles"
        css_dir.mkdir()
        # Create more CSS files than the limit.
        for i in range(10):
            (css_dir / f"page{i}.css").write_text(f".page{i} {{ color: red; }}")

        result = _discover_style_files(str(tmp_path))
        assert len(result["global_css"]) <= 4
