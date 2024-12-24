import copy
import re
import cssutils
from collections import deque
from lxml.html import etree
from io import StringIO

from .utils import ElementNode, TagNameList, MapTagNameList, stringfy_selector
from .active_elements import ActiveElements
import logging
cssutils.log.setLevel(logging.CRITICAL)

class HTMLTree:
    def __init__(self):
        self.elementNodes = [ElementNode] * 100000
        self.rawNode2id: dict = {}
        self.element2id: dict = {}
        self.id2rawNode: dict = {}
        self.valid: list[bool] = [False] * 100000
        self.nodeCounts: int
        self.nodeDict = {}
        self.element_value = {}
        self.invisible_elements=[]
    
    def fetch_html_content(self, html_content) -> str:
        """
        Fetch the html content to extract and prune the DOM tree based on visibility styles.
        """
        self.__init__()
        parser = etree.HTMLParser()
        self.tree = etree.parse(StringIO(html_content), parser)
        self.copy_tree = copy.deepcopy(self.tree)
        root = self.tree.getroot()

        self.init_html_tree(root)
        self.build_html_tree(root)
        return self.prune_tree()
    
    @staticmethod
    def build_node(node, idx: int) -> ElementNode:
        elementNode = ElementNode()
        elementNode["nodeId"] = idx
        elementNode["tagName"] = node.tag
        elementNode["text"] = node.text if node.text else ""
        elementNode["attributes"] = node.attrib
        elementNode["childIds"] = []
        elementNode["parentId"] = ""
        elementNode["siblingId"] = ""
        elementNode["twinId"] = ""
        elementNode["depth"] = 1
        elementNode["htmlContents"] = etree.tostring(
            node, pretty_print=True).decode()
        return elementNode

    def build_mapping(self) -> None:
        self.element2id = {value["nodeId"]: index for index,
                           value in enumerate(self.elementNodes)}
        self.id2rawNode = {str(index): value for value,
                           index in self.rawNode2id.items()}

    def init_html_tree(self, root) -> None:
        node_queue = deque([root])
        node_id = 0
        while node_queue:
            node = node_queue.popleft()
            self.elementNodes[node_id] = HTMLTree().build_node(node, node_id)
            self.rawNode2id[node] = node_id
            node_id += 1
            for child in node.getchildren():
                node_queue.append(child)
        self.build_mapping()
        self.nodeCounts = node_id
        self.valid = self.valid[:self.nodeCounts + 1]

    def build_html_tree(self, root) -> None:
        node_queue = deque([root])
        root_id = self.rawNode2id[root]
        self.elementNodes[root_id]["parentId"] = -1
        while node_queue:
            node = node_queue.popleft()
            parent_id = self.rawNode2id[node]
            tag_st = {}
            sibling_id = 1
            for child in node.getchildren():
                child_id = self.rawNode2id[child]
                tag_name = self.elementNodes[child_id].get("tagName")
                tag_st[tag_name] = tag_st.get(tag_name, 0) + 1
                twin_id = tag_st.get(tag_name)
                self.elementNodes[parent_id]["childIds"].append(child_id)
                self.elementNodes[child_id]["parentId"] = parent_id
                self.elementNodes[child_id]["twinId"] = twin_id
                self.elementNodes[child_id]["depth"] = self.elementNodes[parent_id]["depth"] + 1
                self.elementNodes[child_id]["siblingId"] = sibling_id
                node_queue.append(child)
                sibling_id += 1
        self.pruningTreeNode = copy.deepcopy(self.elementNodes)

    def get_xpath(self, idx: int) -> str:
        locator_str = ""
        current_node = self.elementNodes[idx]
        tag_name = current_node["tagName"]
        twinId = current_node["twinId"]
        locator_str = "/" + tag_name + "[" + str(twinId) + "]"
        while current_node["parentId"] != 0:
            parentid = current_node["parentId"]
            current_node = self.elementNodes[parentid]
            current_tag_name = current_node["tagName"]
            twinId = current_node["twinId"]
            locator_str = "/" + current_tag_name + \
                "[" + str(twinId) + "]" + locator_str
        parentid = current_node["parentId"]
        current_node = self.elementNodes[parentid]
        current_tag_name = current_node["tagName"]
        return "/" + current_tag_name + locator_str

    def get_selector(self, idx: int) -> str:
        selector_parts = []
        current_node = self.pruningTreeNode[idx]
        while current_node["parentId"] != -1:
            tag_name = str(current_node["tagName"])
            if "id" in current_node["attributes"]:
                selector_parts.insert(0, f'#{current_node["attributes"]["id"]}')
                break
            class_name = current_node["attributes"].get("class", "")
            if isinstance(class_name, str):
                class_name = class_name.strip().split()
                class_name.sort()
                class_name = ".".join(class_name)
                if class_name:
                    tag_name += f'.{class_name}'
            # Calculate sibling index
            sibling_index = 1
            parent_id = current_node["parentId"]
            parent_node = self.pruningTreeNode[parent_id]
            for sibling_id in parent_node["childIds"]:
                if sibling_id == current_node["nodeId"]:
                    break
                sibling_index += 1
            if len(parent_node["childIds"]) > 1:
                tag_name += f':nth-child({sibling_index})'
            selector_parts.insert(0, tag_name)
            current_node = parent_node
        return " > ".join(selector_parts)

    def is_valid(self, idx: int) -> bool:
        node = self.pruningTreeNode[idx]
        if node["tagName"] in TagNameList:
            return ActiveElements.is_valid_element(node)
        return False

    def prune_tree(self) -> str:
        """Traverse each element to determine if it is valid and prune"""
        result_list = []
        root = self.pruningTreeNode[0]
        if root is None:
            result_list = []
        stack = [root]
        while stack:
            node = stack.pop()
            nodeId = node["nodeId"]
            result_list.append(nodeId)
            children = []
            for childId in node["childIds"]:
                childNode = self.pruningTreeNode[childId]
                children.append(childNode)
            stack.extend(children)
        result = result_list[::-1]
        for nodeId in result:
            if self.is_valid(nodeId) or self.valid[nodeId] is True:
                rawNode = self.id2rawNode[str(nodeId)]
                html_contents = etree.tostring(
                    rawNode, pretty_print=True).decode()
                self.pruningTreeNode[nodeId]["htmlContents"] = html_contents
                self.valid[nodeId] = True
                current_id = nodeId
                while self.pruningTreeNode[current_id]["parentId"] != -1:
                    parent_id = self.pruningTreeNode[current_id]["parentId"]
                    self.valid[parent_id] = True
                    current_id = parent_id
            else:
                rawNode = self.id2rawNode[str(nodeId)]
                rawNode.getparent().remove(rawNode)
                current_node = self.pruningTreeNode[nodeId]
                current_node["htmlContents"] = ""
                parentid = current_node["parentId"]
                # self.pruningTreeNode[parentid]["childIds"].remove(nodeId)
                for child in self.pruningTreeNode[parentid]["childIds"]:
                    child_node=self.pruningTreeNode[child]
                    self.set_invalid(child_node)
                # self.valid[nodeId] = False
        return self.pruningTreeNode[0]["htmlContents"]

    def get_element_contents(self, idx: int) -> str:
        node = self.elementNodes[idx]
        html_content = node["htmlContents"]
        return html_content
    
    def get_tag_name(self, element: ElementNode) -> (str, int, str):  # type: ignore
        tag_name = ActiveElements.get_element_tagName(element)
        tag_idx = element["nodeId"]
        validContent=HTMLTree().process_element_contents(element)
        
        if tag_name == "span":
            parent_id = element["parentId"]
            if parent_id != -1: 
                parent_node = self.pruningTreeNode[parent_id]
                parent_tag_name, parent_tag_idx, _ = self.get_tag_name(parent_node)
                for sibling_id in parent_node["childIds"]:
                    sibling_node = self.pruningTreeNode[sibling_id]
                    if sibling_node["tagName"] == "span" and sibling_id!=element["nodeId"]:
                        validContent += HTMLTree().process_element_contents(sibling_node)
                        self.set_invalid(sibling_node)  # Mark sibling as invalid
            return parent_tag_name, tag_idx, validContent

        if tag_name == "unknown":
            tag_name = element["tagName"]
            tag_idx = element["nodeId"]
            validContent=HTMLTree().process_element_contents(element)
            if tag_name in MapTagNameList:
                parent_node = self.pruningTreeNode[element["parentId"]]
                parent_tag_name, parent_tag_idx, _ = self.get_tag_name(parent_node)
                return parent_tag_name, tag_idx, validContent
            else:
                return ("statictext", tag_idx, validContent)
        return (tag_name, tag_idx, validContent)

    def set_invalid(self, node: ElementNode) -> None:
        node_id = node["nodeId"]
        self.valid[node_id] = False
        node["htmlContents"] = "" 

    def set_invalid_children(self, node: ElementNode) -> None:
        for child_id in node["childIds"]:
            child_node = self.pruningTreeNode[child_id]
            self.set_invalid_children(child_node) 

    def build_dom_tree(self) -> str:
        root = self.pruningTreeNode[0]
        stack = [(root, 0)]
        contents = ""
        num = 0
        effective_depths = {}
        last_content_depth = -1

        while stack:
            node, actual_depth = stack.pop()

            # Check if the node is visible
            if not ActiveElements.is_visiable(node):
                self.set_invalid(node)
                self.set_invalid_children(node)
                continue
            if self.get_selector(node["nodeId"]) in self.invisible_elements:
                self.set_invalid(node)
                self.set_invalid_children(node)
                continue

            node_has_content = False
            if self.valid[node["nodeId"]]:
                tag_name, tag_idx, validContent = self.get_tag_name(node)
                content_text = validContent if validContent else HTMLTree().process_element_contents(node)
                num += 1
                self.nodeDict[num] = tag_idx

                # Process the node attributes
                attributes = []
                expanded = node["attributes"].get("aria-expanded")
                haspopup = node["attributes"].get("aria-haspopup")
                focused = node["attributes"].get("focused")
                selected = node["attributes"].get("selected")

                # If the node itself has no content, pass attributes to the first descendant with content
                if not node_has_content and (haspopup or expanded):
                    descendant_stack = [node]
                    while descendant_stack:
                        current_node = descendant_stack.pop()
                        for child_id in reversed(current_node["childIds"]): 
                            child_node = self.pruningTreeNode[child_id]
                            child_content = HTMLTree().process_element_contents(child_node)
                            if re.search(r'[a-zA-Z0-9]', child_content):
                                if haspopup:
                                    child_node["attributes"]["aria-haspopup"] = haspopup
                                if expanded:
                                    child_node["attributes"]["aria-expanded"] = expanded
                                descendant_stack = []  # Exit the loop
                                break
                            descendant_stack.append(child_node)

                if haspopup:
                    attributes.append(f" hasPopup: {haspopup}")
                if expanded:
                    attributes.append(f" expanded: {expanded}")
                if focused:
                    attributes.append(f" focused: {focused}")
                if selected:
                    attributes.append(f" selected: {selected}")
                attributes_text = " ".join(attributes)

                # Process the node content
                if node_has_content or re.search(r'[a-zA-Z0-9]', content_text):
                    if actual_depth not in effective_depths:
                        effective_depths[actual_depth] = effective_depths[last_content_depth] + 1 if last_content_depth in effective_depths else 0
                        last_content_depth = actual_depth
                    effective_indent_level = effective_depths[actual_depth]
                    contents += "  " * effective_indent_level + "[" + str(num) + "] " + tag_name + \
                                " " + f"'{content_text}'" + attributes_text + "\n"
                    self.element_value[str(tag_idx)] = content_text
                    node_has_content = True
                    # If the node is not expanded, skip it
                    if expanded == "false":
                        continue
            if node_has_content and (actual_depth + 1) not in effective_depths:
                effective_depths[actual_depth + 1] = effective_indent_level + 1
            children = [self.pruningTreeNode[child_id] for child_id in node["childIds"]]
            for child in reversed(children):
                stack.append((child, actual_depth + 1))
        return contents
    
    def get_selector_and_xpath(self, idx: int) -> (str, str):  # type: ignore
        try:
            selector = self.get_selector(idx)
            xpath = self.get_xpath(idx)
            return selector, xpath
        except:
            print(f"can't locate element")

    @staticmethod
    def process_element_contents(element: ElementNode) -> str:
        # TODO Add appropriate interactive element information, currently only processing interactive elements with text attributes
        html_text = ActiveElements.get_element_value(element)
        if html_text is None:
            return ""
        return html_text.replace("\n", "").replace("\t", "").strip()

    def get_element_value(self, element_id: int) -> str:
        return self.element_value[str(element_id)]


__all__ = [
    "HTMLTree"
]