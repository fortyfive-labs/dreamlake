"""GraphQL convenience methods and the explicit legacy metric-read error."""

from typing import Optional, Dict, Any

from .exceptions import ConfigurationError

def _index_read_unsupported(method: str) -> ConfigurationError:
    """The error for the legacy remote metric reads, which paged by a server-assigned index."""
    return ConfigurationError(
        f"{method} is no longer supported for remote metrics: the server does not assign "
        f"point indices or chunk numbers, so they cannot be mapped to stored rows. Read "
        f"committed rows by cursor instead: exp.metrics(name).read_rows() or "
        f"iter_row_blocks(), or RemoteClient.read_metric_rows(). Local reads with "
        f"exp.metrics(name).read() are unchanged."
    )

class RemoteClient:

    def list_projects_graphql(
            self,
            namespace_slug: Optional[str] = None,
            limit: int = 50,
            offset: int = 0,
        ) -> Dict[str, Any]:
            """
            List projects via GraphQL (single page).

            Args:
                namespace_slug: Namespace to list projects for (defaults to authenticated user's namespace)
                limit: Page size
                offset: Starting offset

            Returns:
                Dict with 'projects' list and 'totalCount' int

            Raises:
                httpx.HTTPStatusError: If request fails
            """
            query = """
            query ProjectsPaginated($namespaceSlug: String, $limit: Int!, $offset: Int!) {
              projectsPaginated(namespaceSlug: $namespaceSlug, limit: $limit, offset: $offset) {
                projects {
                  id
                  name
                  slug
                  description
                  tags
                  experimentCount
                }
                totalCount
                hasMore
              }
            }
            """
            result = self.graphql_query(query, {
                "namespaceSlug": namespace_slug,
                "limit": limit,
                "offset": offset,
            })
            page = result.get("projectsPaginated", {})
            return {
                "projects": page.get("projects", []),
                "totalCount": page.get("totalCount", 0),
            }

    def list_experiments_graphql(
            self,
            project_slug: str,
            status: Optional[str] = None,
            namespace_slug: Optional[str] = None,
            limit: int = 50,
            offset: int = 0,
        ) -> Dict[str, Any]:
            """
            List experiments in a project via GraphQL (single page).

            Args:
                project_slug: Project slug
                status: Optional experiment status filter (RUNNING, COMPLETED, FAILED, CANCELLED)
                namespace_slug: Namespace slug (defaults to authenticated user's namespace)
                limit: Page size
                offset: Starting offset

            Returns:
                Dict with 'experiments' list and 'totalCount' int

            Raises:
                httpx.HTTPStatusError: If request fails
            """
            query = """
            query ExperimentsPaginated($namespaceSlug: String, $projectSlug: String!, $status: ExperimentStatus, $limit: Int!, $offset: Int!) {
              experimentsPaginated(namespaceSlug: $namespaceSlug, projectSlug: $projectSlug, status: $status, limit: $limit, offset: $offset) {
                experiments {
                  id
                  name
                  description
                  tags
                  status
                  startedAt
                  endedAt
                  metadata
                  project {
                    slug
                    namespace {
                      slug
                    }
                  }
                  metrics {
                    name
                  }
                  files {
                    id
                  }
                  trackCount
                  displayPath
                }
                totalCount
                hasMore
              }
            }
            """
            variables: Dict[str, Any] = {
                "namespaceSlug": namespace_slug,
                "projectSlug": project_slug,
                "limit": limit,
                "offset": offset,
            }
            if status is not None:
                variables["status"] = status

            result = self.graphql_query(query, variables)
            page = result.get("experimentsPaginated", {})
            return {
                "experiments": page.get("experiments", []),
                "totalCount": page.get("totalCount", 0),
            }

    def get_experiment_graphql(
            self, project_slug: str, experiment_name: str, namespace_slug: Optional[str] = None
        ) -> Optional[Dict[str, Any]]:
            """
            Get a single experiment via GraphQL.

            Args:
                project_slug: Project slug
                experiment_name: Experiment name
                namespace_slug: Namespace slug (optional - defaults to client's namespace)

            Returns:
                Experiment dict with metadata, or None if not found

            Raises:
                httpx.HTTPStatusError: If request fails
            """
            query = """
            query Experiment($namespaceSlug: String, $projectSlug: String!, $experimentName: String!) {
              experiment(namespaceSlug: $namespaceSlug, projectSlug: $projectSlug, experimentName: $experimentName) {
                id
                name
                description
                tags
                status
                metadata
                project {
                  slug
                  namespace {
                    slug
                  }
                }
                metrics {
                  name
                }
                files {
                  id
                  name
                  pPath
                  description
                  tags
                  metadata
                  physicalFile {
                    filename
                    contentType
                    sizeBytes
                    checksum
                    s3Url
                  }
                }
                parameters {
                  id
                  data
                }
              }
            }
            """
            # Use provided namespace or fall back to client's namespace
            ns = namespace_slug or self.namespace

            variables = {
                "namespaceSlug": ns,
                "projectSlug": project_slug,
                "experimentName": experiment_name
            }

            result = self.graphql_query(query, variables)
            return result.get("experiment")

    def get_experiment_by_path_graphql(
            self, project_slug: str, experiment_path: str, namespace_slug: Optional[str] = None
        ) -> Optional[Dict[str, Any]]:
            """
            Get experiment by traversing the project hierarchy path.

            Args:
                project_slug: Project slug
                experiment_path: Full path to experiment (e.g., "folder/subfolder/experiment")
                namespace_slug: Namespace slug (optional - defaults to client's namespace)

            Returns:
                Experiment dict if found, None otherwise
            """
            namespace_slug = namespace_slug or self.namespace

            # Query project hierarchy
            query = """
            query GetProjectHierarchy($namespaceSlug: String!, $projectSlug: String!) {
              project(namespaceSlug: $namespaceSlug, projectSlug: $projectSlug) {
                nodes(parentId: null, maxDepth: 20) {
                  ...NodeFields
                  children {
                    ...NodeFields
                    children {
                      ...NodeFields
                      children {
                        ...NodeFields
                        children {
                          ...NodeFields
                          children {
                            ...NodeFields
                            children {
                              ...NodeFields
                              children {
                                ...NodeFields
                                children {
                                  ...NodeFields
                                  children {
                                    ...NodeFields
                                  }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }

            fragment NodeFields on Node {
              id
              name
              type
              pPath
              experimentId
            }
            """

            result = self.graphql_query(query, {"namespaceSlug": namespace_slug, "projectSlug": project_slug})
            nodes = result.get("project", {}).get("nodes", [])

            # Traverse hierarchy to find experiment by path
            def find_experiment_by_path(nodes, path_parts, depth=0):
                if not path_parts:
                    return None

                current_part = path_parts[0]
                remaining_parts = path_parts[1:]

                for node in nodes:
                    # Match current path part with node name
                    if node.get("name") == current_part:
                        # If this is the last part and it's an experiment, return it
                        if not remaining_parts and node.get("type") == "EXPERIMENT":
                            # Get full experiment details
                            experiment_id = node.get("experimentId")
                            if experiment_id:
                                # Query full experiment details
                                exp_query = """
                                query GetExperiment($id: ID!) {
                                  experimentById(id: $id) {
                                    id
                                    name
                                    description
                                    tags
                                    status
                                    metadata
                                    project {
                                      slug
                                      namespace {
                                        slug
                                      }
                                    }
                                  }
                                }
                                """
                                exp_result = self.graphql_query(exp_query, {"id": experiment_id})
                                return exp_result.get("experimentById")
                            return None

                        # If there are more parts, traverse children
                        if remaining_parts and node.get("children"):
                            found = find_experiment_by_path(node["children"], remaining_parts, depth + 1)
                            if found:
                                return found

                return None

            path_parts = experiment_path.strip("/").split("/")
            return find_experiment_by_path(nodes, path_parts)

    def search_experiments_graphql(
            self,
            pattern: str,
            limit: int = 50,
            offset: int = 0,
        ) -> Dict[str, Any]:
            """
            Search experiments using glob pattern via GraphQL (single page).

            Pattern format: namespace/project/experiment
            Supports wildcards: *, ?, [0-9], [a-z], etc.

            Args:
                pattern: Glob pattern (e.g., "tom*/tutorials/*", "*/project-?/exp*")
                limit: Page size
                offset: Starting offset

            Returns:
                Dict with 'experiments' list and 'totalCount' int

            Raises:
                httpx.HTTPStatusError: If request fails

            Examples:
                >>> client.search_experiments_graphql("tom*/tutorials/*")
                >>> client.search_experiments_graphql("*/my-project/baseline*")
            """
            query = """
            query SearchExperimentsPaginated($pattern: String!, $limit: Int!, $offset: Int!) {
              searchExperimentsPaginated(pattern: $pattern, limit: $limit, offset: $offset) {
                experiments {
                  id
                  name
                  description
                  tags
                  status
                  startedAt
                  endedAt
                  metadata
                  project {
                    id
                    slug
                    name
                    namespace {
                      id
                      slug
                    }
                  }
                  metrics {
                    name
                  }
                  files {
                    id
                    name
                  }
                  trackCount
                  displayPath
                }
                totalCount
                hasMore
              }
            }
            """
            result = self.graphql_query(query, {"pattern": pattern, "limit": limit, "offset": offset})
            page = result.get("searchExperimentsPaginated", {})
            return {
                "experiments": page.get("experiments", []),
                "totalCount": page.get("totalCount", 0),
            }
