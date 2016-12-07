# -*- coding: utf-8 -*-

import os
import time
import tempfile
import shutil
from celery import Celery
from distutils.dir_util import copy_tree
import psycopg2
from subprocess import Popen, PIPE
from os import path, remove

env=os.environ
CELERY_BROKER_URL = env.get('CELERY_BROKER_URL','redis://localhost:6379'),
CELERY_RESULT_BACKEND = env.get('CELERY_RESULT_BACKEND','redis://localhost:6379')

FONCIER_EXTRACTS_DIR = env.get('FONCIER_EXTRACTS_DIR', '/tmp')
FONCIER_STATIC_DIR = env.get('FONCIER_STATIC_DIR')

PG_CONNECT_STRING = env.get("PG_CONNECT_STRING")

taskmanager = Celery('extractions',
                     broker=CELERY_BROKER_URL,
                     backend=CELERY_RESULT_BACKEND)



def run_command(args):
    """
    Run command specified by args. If exit code is not 0 then full command line, STDOUT, STDERR are printed and an
    Exception is raised
    :param args: array of argument
    :return: None
    """
    p = Popen(args, stdout=PIPE, stderr=PIPE)
    p.wait()

    if p.returncode != 0:
        print("Commande : %s" % " ".join(args))
        print("Exit code : %s" % p.returncode)
        print("STDOUT : %s" % p.stdout.read().decode())
        print("STDERR : %s" % p.stderr.read().decode())
        raise Exception("Error running %s" % " ".join(args))


def get_all_tables(conn, year):
    """
    List tables in schema foncier_<year> where year is the second argument
    :param conn: a psycopg connection instance
    :param year: numerix year to append to 'foncier_' to build schema name
    :return: array of table name
    """
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'foncier_%s'" % year)
    res = [res[0] for res in cur.fetchall()]
    cur.close()
    return res


def export_schema_to_shapefile_or_mapfile(year, proj, output_dir, format, conn, pg_connect_string):
    """
    Extract all table in schema foncier_<year> where 'year' is the first argument and generates files according to
    'format' in 'output_dir' folder.
    :param year: numeric year to append to 'foncier_' to build schema name
    :param proj: output projection
    :param output_dir: folder where files will be writen
    :param format: ogr2ogr file format like : "ESRI Shapefile" or "MapInfo File"
    :param conn: a psycopg connection instance
    :param pg_connect_string: a string that contains option to connect to database with ogr2ogr. 'schema' option will
    be added
    :return: None
    """
    for table in get_all_tables(conn, year):
        args = ["ogr2ogr", 
                "-a_srs", "EPSG:%s" % proj,
                "-t_srs", "EPSG:%s" % proj,
                "-f", format, output_dir,
                "PG:%s schemas=foncier_%s" % (PG_CONNECT_STRING, year),
                table]
        run_command(args)


def export_schema_to_sql(year, proj, output_dir, conn, pg_connect_string):
    """
    Extract all table in schema foncier_<year> where 'year' is the first argument and one sql file to create schema,
    tables and inserts data
    :param year: numeric year to append to 'foncier_' to build schema name
    :param proj: output projection
    :param output_dir: folder where files will be writen
    :param conn: a psycopg connection instance
    :param pg_connect_string: a string that contains option to connect to database with ogr2ogr. 'schema' option will
    be added
    :return: None
    """
    with open(path.join(output_dir, "foncier_%s.sql"  % year), 'wb') as f:
        f.write(("CREATE SCHEMA foncier_%s;\n" % year).encode())

        for table in get_all_tables(conn, year):
            table_output_file = path.join(output_dir, "export_table_%s.sql" % table)
            args = ["ogr2ogr",
                    "-a_srs", "EPSG:%s" % proj,
                    "-t_srs", "EPSG:%s" % proj,
                    "-f", "PGDump", table_output_file,
                    "PG:%s schemas=foncier_%s" % (PG_CONNECT_STRING, year),
                    table,
                    "-lco", "SCHEMA=foncier_%s" % year,
                    "-lco", "SRID=4326",
                    "-lco", "CREATE_SCHEMA=off",
                    "-lco", "DROP_TABLE=off"]
            run_command(args)

            with open(table_output_file, 'rb') as table_file:
                f.write(table_file.read())
            remove(table_output_file)


@taskmanager.task(name='extraction.do')
def do(year, format, proj, email, cities):

    tmpdir = tempfile.mkdtemp(dir = FONCIER_EXTRACTS_DIR, prefix = 'foncier_{0}_{1}_{2}_{3}-'.format(year, format, proj, do.request.id))
    print('Created temp dir %s' % tmpdir)

    if (FONCIER_STATIC_DIR is not None):
        try:
            copy_tree(FONCIER_STATIC_DIR, tmpdir)
        except IOError as e:
            print('IOError copying %s to %s' % (FONCIER_STATIC_DIR, tmpdir))

    # connect to DB
    conn = psycopg2.connect(PG_CONNECT_STRING)

    # TODO sanitize input 

    # launch extraction
    print("Format : %s" % format)
    if format == "shp" :
        export_schema_to_shapefile_or_mapfile(year, proj, tmpdir, "ESRI Shapefile", conn, PG_CONNECT_STRING)
    elif format == "mifmid" :
        export_schema_to_shapefile_or_mapfile(year, proj, tmpdir, "MapInfo File", conn, PG_CONNECT_STRING)
    elif format == "postgis" :
        export_schema_to_sql(year, proj, tmpdir, conn, PG_CONNECT_STRING)
    else:
        raise Exception("Invalid format : %s" % format)

    # close DB connection
    conn.close()

    # zip file:
    try:
        name = shutil.make_archive(tmpdir, 'tar')
    except IOError as e:
        print('IOError while zipping %s' % tmpdir)

    # delete directory after zipping:
    shutil.rmtree(tmpdir)
    print('Removed dir %s' % tmpdir)

    # return zip file
    time.sleep(10)
    return 'done with %s ! We should now send an email to %s with a link to %s' % (cities, email, name)

