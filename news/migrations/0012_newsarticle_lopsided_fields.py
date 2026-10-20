from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("news", "0011_newsarticle_article_url_newsarticle_remote_image_url_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="newssource",
            name="bias",
            field=models.CharField(blank=True, default="center", max_length=30),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="left_coverage",
            field=models.IntegerField(default=33),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="center_coverage",
            field=models.IntegerField(default=34),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="right_coverage",
            field=models.IntegerField(default=33),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="dominant_bias",
            field=models.CharField(
                choices=[
                    ("left", "Left-Leaning"),
                    ("center", "Center-Focused"),
                    ("right", "Right-Leaning"),
                    ("polarized", "Polarized / Center Blindspot"),
                    ("balanced", "Balanced"),
                ],
                default="balanced",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="perspectives",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
